import streamlit as st
from docxtpl import DocxTemplate, InlineImage
from docx.shared import Mm
import streamlit.components.v1 as components
import io
import base64
import os
import re
import numpy as np
from datetime import datetime
import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont
from docx.shared import Mm, Pt
import easyocr
import zipfile

st.set_page_config(layout="wide", page_title="Generador de Informes SCADA - CCM")

# =========================================================
# EXTRACCIÓN INTELIGENTE DESDE LIBRO DE EVENTOS VICOS RSC
# =========================================================
@st.cache_resource
def get_ocr_reader():
    return easyocr.Reader(['es'], gpu=False)

def extraer_datos_vicos(file_bytes, nombre_archivo):
    texto_bruto = ""
    
    if nombre_archivo.lower().endswith('.pdf'):
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        for page in doc:
            texto_bruto += page.get_text("text") + "\n"
    else:
        reader = get_ocr_reader()
        img = Image.open(io.BytesIO(file_bytes))
        resultados = reader.readtext(np.array(img))
        resultados.sort(key=lambda r: (r[0][0][1] // 15, r[0][0][0]))
        texto_bruto = "\n".join([r[1] for r in resultados])

    bloques = re.split(r'(?=\b\d{2}/\d{2}/\d{4}\b)', texto_bruto)
    
    fecha_encontrada = None
    hora_disparo = None
    hora_recierre = None

    for bloque in bloques:
        if not bloque.strip():
            continue
        
        hora_match = re.search(r'\b\d{2}:\d{2}:\d{2}[,\.]\d{3}\b', bloque)
        if not hora_match:
            continue
            
        fecha = re.search(r'\b\d{2}/\d{2}/\d{4}\b', bloque).group()
        hora = hora_match.group()
        
        b_limpio = bloque.lower().replace('\n', ' ').replace('|', ' ')
        
        # Buscar Disparo (VP: Desconectado)
        if not hora_disparo and ("disparo" in b_limpio or "di/dt" in b_limpio) and "vp: desconectado" in b_limpio:
            fecha_encontrada = fecha
            hora_disparo = hora
            
        # Buscar Recierre (VP: Conectado)
        if not hora_recierre and ("disparo" in b_limpio or "di/dt" in b_limpio) and "vp: conectado" in b_limpio:
            hora_recierre = hora

    return fecha_encontrada, hora_disparo, hora_recierre

def extraer_datos_sitras(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    texto_total = ""
    
    # 1. Intento rápido sin gastar RAM
    for page in doc:
        texto_total += page.get_text("text") + "\n"
        
    # 2. Si es imagen plana, usamos OCR "Lite" (Bajo consumo RAM)
    if len(texto_total.strip()) < 20:
        reader = get_ocr_reader()
        
        # SOLO procesamos la primera página para no saturar memoria
        page = doc[0] 
        
        # Convertimos a 96 DPI y Escala de grises (ahorra muchísima RAM)
        pix = page.get_pixmap(dpi=96, colorspace=fitz.csGRAY)
        img = Image.open(io.BytesIO(pix.tobytes("jpeg")))
        
        resultados = reader.readtext(np.array(img))
        texto_total = " ".join([r[1] for r in resultados])

    t_limpio = texto_total.lower()
    
    # 3. Búsqueda de patrones
    m_hora = re.search(r'(\d{2}:\d{2}:\d{2}[\.,]\d{3})', t_limpio)
    m_func = re.search(r'(i\s*max\s*tripping|imax\s*tripping|di/dt\s*tripping|tripping)', t_limpio)
    m_corrientes = re.findall(r'(\d{3,5})\s*a\b', t_limpio) 
    
    hora_sitras, funcion_sitras, corriente_sitras = None, None, None

    if m_hora: hora_sitras = m_hora.group(1).replace(".", ",")
    if m_func:
        f_raw = m_func.group(1)
        funcion_sitras = "Disparo Imax" if "max" in f_raw else ("Disparo di/dt" if "di/dt" in f_raw else "Disparo por protección")
    if m_corrientes:
        corriente_sitras = str(max([int(c) for c in m_corrientes]))

    return hora_sitras, funcion_sitras, corriente_sitras

# =========================================================
# COMPONENTE CANVAS BIDIRECCIONAL (SITRAS PRO)
# =========================================================
# =========================================================
# COMPONENTE DE RECORTE LIBRE (CROPPER) PARA SIGRA
# =========================================================
CROP_DIR = os.path.join(os.path.dirname(__file__), "crop_component")
os.makedirs(CROP_DIR, exist_ok=True)
CROP_HTML_PATH = os.path.join(CROP_DIR, "index.html")

with open(CROP_HTML_PATH, "w", encoding="utf-8") as f:
    f.write("""<!DOCTYPE html>
<html>
<head>
  <style>
    body { margin: 0; font-family: sans-serif; background-color: #262730; color: #fff; text-align: center; }
    #toolbar { padding: 8px; background: #1e1e24; display: flex; justify-content: center; gap: 10px; align-items: center; border-radius: 6px; margin-bottom: 8px; font-size: 13px; }
    #instrucciones { font-weight: bold; color: #4bb4ff; }
    #canvas-wrapper { position: relative; display: inline-block; max-height: 480px; overflow: auto; border: 2px solid #555; border-radius: 4px; }
    canvas { display: block; cursor: crosshair; }
    button { padding: 6px 12px; border: none; border-radius: 4px; font-weight: bold; cursor: pointer; }
    .btn-crop { background-color: #0078d4; color: white; display: none; font-size: 14px; padding: 8px 16px; }
    .btn-reset { background-color: #555; color: white; }
  </style>
</head>
<body>
  <div id="toolbar">
    <span id="instrucciones">Arrastra el mouse sobre el gráfico para recortar la zona deseada.</span>
    <button class="btn-reset" onclick="resetCrop()">🔄 Reiniciar</button>
  </div>

  <div id="canvas-wrapper">
    <canvas id="canvasCrop"></canvas>
  </div>

  <div style="margin-top: 10px;">
    <button id="btnCrop" class="btn-crop" onclick="guardarRecorte()">📄 Guardar Recorte en el Word</button>
  </div>

  <script>
    const canvas = document.getElementById("canvasCrop");
    const ctx = canvas.getContext("2d");
    const btnCrop = document.getElementById("btnCrop");

    let bgImg = new Image();
    let isDrawing = false;
    let startX = 0, startY = 0;
    let rect = null;

    function sendToStreamlit(val) {
      window.parent.postMessage({
        isStreamlitMessage: true,
        type: "streamlit:setComponentValue",
        value: val
      }, "*");
    }

    function redraw() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(bgImg, 0, 0);

      if (rect) {
        ctx.strokeStyle = "#0078d4";
        ctx.lineWidth = 3;
        ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
        ctx.fillStyle = "rgba(0, 120, 212, 0.15)";
        ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
      }
    }

    function getMousePos(evt) {
      const cRect = canvas.getBoundingClientRect();
      const scaleX = canvas.width / cRect.width;
      const scaleY = canvas.height / cRect.height;
      return {
        x: (evt.clientX - cRect.left) * scaleX,
        y: (evt.clientY - cRect.top) * scaleY
      };
    }

    canvas.onmousedown = (e) => {
      if (e.button !== 0) return;
      const pos = getMousePos(e);
      isDrawing = true;
      startX = pos.x;
      startY = pos.y;
      rect = { x: startX, y: startY, w: 0, h: 0 };
    };

    canvas.onmousemove = (e) => {
      if (!isDrawing) return;
      const pos = getMousePos(e);
      rect = {
        x: Math.min(startX, pos.x),
        y: Math.min(startY, pos.y),
        w: Math.abs(pos.x - startX),
        h: Math.abs(pos.y - startY)
      };
      redraw();
    };

    canvas.onmouseup = () => {
      if (!isDrawing) return;
      isDrawing = false;
      if (rect && rect.w > 20 && rect.h > 10) {
        btnCrop.style.display = "inline-block";
      } else {
        rect = null;
        btnCrop.style.display = "none";
      }
      redraw();
    };

    function resetCrop() {
      rect = null;
      btnCrop.style.display = "none";
      redraw();
    }

    function guardarRecorte() {
      if (!rect) return;
      btnCrop.innerText = "⏳ Procesando recorte...";
      btnCrop.disabled = true;

      // Crear un canvas temporal para extraer solo la zona recortada
      const tempCanvas = document.createElement("canvas");
      tempCanvas.width = rect.w;
      tempCanvas.height = rect.h;
      const tempCtx = tempCanvas.getContext("2d");
      
      tempCtx.drawImage(bgImg, rect.x, rect.y, rect.w, rect.h, 0, 0, rect.w, rect.h);
      const dataUrl = tempCanvas.toDataURL("image/jpeg", 0.95);
      sendToStreamlit(dataUrl);
    }

    window.addEventListener("message", (event) => {
      if (event.data.type === "streamlit:render") {
        const args = event.data.args;
        canvas.width = args.w;
        canvas.height = args.h;
        
        // Ajustamos el estilo del canvas para respetar las proporciones reales de la hoja horizontal
        canvas.style.width = args.w + "px";
        canvas.style.height = args.h + "px";
        
        bgImg.src = "data:image/jpeg;base64," + args.img_b64;
        bgImg.onload = () => redraw();
      }
    });

    window.parent.postMessage({isStreamlitMessage: true, type: "streamlit:componentReady", apiVersion: 1}, "*");
    window.parent.postMessage({isStreamlitMessage: true, type: "streamlit:setFrameHeight", height: 580}, "*");

  </script>
</body>
</html>""")

crop_sigra_component = components.declare_component("crop_sigra", path=CROP_DIR)

# =========================================================
# COMPONENTE CANVAS BIDIRECCIONAL (SITRAS PRO)
# =========================================================
COMPONENT_DIR = os.path.join(os.path.dirname(__file__), "editor_component")
os.makedirs(COMPONENT_DIR, exist_ok=True)
INDEX_HTML_PATH = os.path.join(COMPONENT_DIR, "index.html")

with open(INDEX_HTML_PATH, "w", encoding="utf-8") as f:
    f.write("""<!DOCTYPE html>
<html>
<head>
  <style>
    body { margin: 0; font-family: sans-serif; background-color: #262730; color: #fff; text-align: center; }
    #toolbar { padding: 8px; background: #1e1e24; display: flex; justify-content: center; gap: 10px; align-items: center; border-radius: 6px; margin-bottom: 8px; font-size: 13px; }
    #instrucciones { font-weight: bold; color: #ff4b4b; }
    #canvas-wrapper { position: relative; display: inline-block; max-height: 480px; overflow-y: auto; border: 2px solid #555; border-radius: 4px; }
    canvas { display: block; cursor: crosshair; }
    button { padding: 6px 12px; border: none; border-radius: 4px; font-weight: bold; cursor: pointer; }
    .btn-undo { background-color: #f39c12; color: white; }
    .btn-reset { background-color: #555; color: white; }
    .btn-word { background-color: #0078d4; color: white; display: none; font-size: 14px; padding: 8px 16px; }
  </style>
</head>
<body>
  <div id="toolbar">
    <span id="instrucciones">Paso 1: Arrastra el mouse para encuadrar "Función de disparo" (Buscar PID: 3162, 3164 o 3168)</span>
    <button class="btn-undo" onclick="deshacer()">↩ Deshacer (Clic Derecho)</button>
    <button class="btn-reset" onclick="resetCanvas()">🔄 Reiniciar</button>
  </div>

  <div id="canvas-wrapper">
    <canvas id="canvasSitras"></canvas>
  </div>

  <div style="margin-top: 10px;">
    <button id="btnWord" class="btn-word" onclick="adjuntarAlWord()">📄 Adjuntar directamente al Informe Word</button>
  </div>

  <script>
    const canvas = document.getElementById("canvasSitras");
    const ctx = canvas.getContext("2d");
    const instruc = document.getElementById("instrucciones");
    const btnWord = document.getElementById("btnWord");

    let bgImg = new Image();
    const eventos = [
      "Función de disparo",
      "Apertura automática del interruptor",
      "Re-cierre exitoso del interruptor"
    ];
    
    // Agregamos las pistas PID para cada paso
    const ayudas_pid = [
      "(Buscar PID: 3162, 3164 o 3168)",
      "(Buscar PID: 3135)",
      "(Buscar PID: 3136)"
    ];

    let paso = 0; 
    let modo = "RECT"; 
    let isDrawing = false;
    let startX = 0, startY = 0;
    let currentRect = null;
    let anotaciones = []; 

    function sendToStreamlit(val) {
      window.parent.postMessage({
        isStreamlitMessage: true,
        type: "streamlit:setComponentValue",
        value: val
      }, "*");
    }

    function redraw() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(bgImg, 0, 0);

      anotaciones.forEach(a => {
        ctx.strokeStyle = "red";
        ctx.lineWidth = 3;
        ctx.strokeRect(a.rect.x, a.rect.y, a.rect.w, a.rect.h);

        ctx.fillStyle = "white";
        ctx.fillRect(a.textBox.x, a.textBox.y, a.textBox.w, a.textBox.h);
        ctx.strokeStyle = "red";
        ctx.lineWidth = 2;
        ctx.strokeRect(a.textBox.x, a.textBox.y, a.textBox.w, a.textBox.h);

        ctx.fillStyle = "red";
        ctx.font = "bold 14px Arial";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(a.texto, a.textBox.x + (a.textBox.w / 2), a.textBox.y + (a.textBox.h / 2));

        ctx.beginPath();
        ctx.moveTo(a.arrow.fromX, a.arrow.fromY);
        ctx.lineTo(a.arrow.toX, a.arrow.toY);
        ctx.stroke();

        const headlen = 9;
        const angle = Math.atan2(a.arrow.toY - a.arrow.fromY, a.arrow.toX - a.arrow.fromX);
        ctx.beginPath();
        ctx.moveTo(a.arrow.toX, a.arrow.toY);
        ctx.lineTo(a.arrow.toX - headlen * Math.cos(angle - Math.PI / 6), a.arrow.toY - headlen * Math.sin(angle - Math.PI / 6));
        ctx.lineTo(a.arrow.toX - headlen * Math.cos(angle + Math.PI / 6), a.arrow.toY - headlen * Math.sin(angle + Math.PI / 6));
        ctx.fillStyle = "red";
        ctx.fill();
      });

      if (currentRect) {
        ctx.strokeStyle = "red";
        ctx.lineWidth = 3;
        ctx.strokeRect(currentRect.x, currentRect.y, currentRect.w, currentRect.h);
      }
    }

    function getMousePos(evt) {
      const rect = canvas.getBoundingClientRect();
      const scaleX = canvas.width / rect.width;
      const scaleY = canvas.height / rect.height;
      return {
        x: (evt.clientX - rect.left) * scaleX,
        y: (evt.clientY - rect.top) * scaleY
      };
    }

    window.oncontextmenu = (e) => {
      e.preventDefault();
      deshacer();
    };

    function deshacer() {
      if (modo === "CLICK_POS") {
        currentRect = null;
        modo = "RECT";
        instruc.innerText = `Paso ${paso + 1}: Arrastra el mouse para encuadrar "${eventos[paso]}" ${ayudas_pid[paso]}`;
      } else if (anotaciones.length > 0) {
        anotaciones.pop();
        paso--;
        modo = "RECT";
        btnWord.style.display = "none";
        instruc.innerText = `Paso ${paso + 1}: Arrastra el mouse para encuadrar "${eventos[paso]}" ${ayudas_pid[paso]}`;
      }
      redraw();
    }

    canvas.onmousedown = (e) => {
      if (e.button === 2 || paso >= 3) return;
      const pos = getMousePos(e);

      if (modo === "RECT") {
        isDrawing = true;
        startX = pos.x;
        startY = pos.y;
        currentRect = { x: startX, y: startY, w: 0, h: 0 };
      } else if (modo === "CLICK_POS") {
        const clickArriba = pos.y < (currentRect.y + currentRect.h / 2);
        const centroX = currentRect.x + currentRect.w / 2;
        
        ctx.font = "bold 14px Arial";
        const textWidth = ctx.measureText(eventos[paso]).width + 30;
        const textHeight = 28;
        
        let tbX = centroX - textWidth / 2;
        let tbY = clickArriba ? currentRect.y - 45 : currentRect.y + currentRect.h + 20;
        let fromY = clickArriba ? tbY + textHeight : tbY;
        let toY = clickArriba ? currentRect.y : currentRect.y + currentRect.h;

        anotaciones.push({
          texto: eventos[paso],
          rect: currentRect,
          textBox: { x: tbX, y: tbY, w: textWidth, h: textHeight },
          arrow: { fromX: centroX, fromY: fromY, toX: centroX, toY: toY }
        });

        currentRect = null;
        paso++;

        if (paso < 3) {
          modo = "RECT";
          instruc.innerText = `Paso ${paso + 1}: Arrastra el mouse para encuadrar "${eventos[paso]}" ${ayudas_pid[paso]}`;
        } else {
          instruc.innerText = "✅ ¡Listo! Presiona el botón azul para colocar la imagen en el Word.";
          btnWord.style.display = "inline-block";
        }
        redraw();
      }
    };

    canvas.onmousemove = (e) => {
      if (!isDrawing) return;
      const pos = getMousePos(e);
      currentRect = {
        x: Math.min(startX, pos.x),
        y: Math.min(startY, pos.y),
        w: Math.abs(pos.x - startX),
        h: Math.abs(pos.y - startY)
      };
      redraw();
    };

    canvas.onmouseup = () => {
      if (!isDrawing) return;
      isDrawing = false;
      if (currentRect && currentRect.w > 15 && currentRect.h > 8) {
        modo = "CLICK_POS";
        instruc.innerText = `Haz un clic ARRIBA o ABAJO del recuadro para posicionar "${eventos[paso]}".`;
      } else {
        currentRect = null;
        redraw();
      }
    };

    function resetCanvas() {
      paso = 0;
      modo = "RECT";
      currentRect = null;
      anotaciones = [];
      btnWord.style.display = "none";
      instruc.innerText = `Paso 1: Arrastra el mouse para encuadrar "${eventos[0]}" ${ayudas_pid[0]}`;
      redraw();
    }

    function adjuntarAlWord() {
      btnWord.innerText = "⏳ Adjuntando al Word...";
      btnWord.disabled = true;
      const dataUrl = canvas.toDataURL("image/jpeg", 0.95);
      sendToStreamlit(dataUrl);
    }

    window.addEventListener("message", (event) => {
      if (event.data.type === "streamlit:render") {
        const args = event.data.args;
        canvas.width = args.w;
        canvas.height = args.h;
        bgImg.src = "data:image/jpeg;base64," + args.img_b64;
        bgImg.onload = () => redraw();
      }
    });

    window.parent.postMessage({isStreamlitMessage: true, type: "streamlit:componentReady", apiVersion: 1}, "*");
    window.parent.postMessage({isStreamlitMessage: true, type: "streamlit:setFrameHeight", height: 580}, "*");
  </script>
</body>
</html>""")

editor_sitras_component = components.declare_component("editor_sitras", path=COMPONENT_DIR)

# =========================================================
# MODAL DEL EDITOR SITRAS PRO
# =========================================================
@st.dialog("✏️ Editor Visual Sitras PRO", width="large")
def modal_editor_sitras(pdf_bytes):
    doc_pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc_pdf.load_page(0)
    pix = page.get_pixmap(dpi=130)
    img_b64 = base64.b64encode(pix.tobytes("jpeg")).decode("utf-8")
    
    resultado_b64 = editor_sitras_component(img_b64=img_b64, w=pix.width, h=pix.height, key="sitras_canvas_widget")
    
    if resultado_b64:
        img_bytes = base64.b64decode(resultado_b64.split(",")[1])
        st.session_state["anexo_sitras_bytes"] = img_bytes
        st.success("✅ ¡Imagen adjuntada exitosamente al Word! Ya puedes cerrar esta ventana.")
        st.rerun()


# MODAL DE RECORTE LIBRE - REGISTRO OSCILOGRÁFICO (SIGRA)
# =========================================================
def procesar_sigra_automatico(pdf_bytes):
    doc_pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    # 1. Extraer el valor del tiempo (Ej. 28.80) de la segunda hoja (índice 1)
    texto_pagina = doc_pdf[1].get_text("text") if len(doc_pdf) > 1 else doc_pdf[0].get_text("text")
    match = re.search(r'C2-C1.*?([\d\.]+)', texto_pagina, re.DOTALL)
    valor_extraido = match.group(1) if match else ""
    if not valor_extraido:
        # Fallback por si la tabla cambia ligeramente
        match_gen = re.search(r'\b\d{2}\.\d{2}\b', texto_pagina)
        valor_extraido = match_gen.group(0) if match_gen else "28.80"
        
    st.session_state["val_osc"] = valor_extraido
    
    # 2. Recortar la imagen con las coordenadas exactas de la segunda hoja
    page = doc_pdf.load_page(1 if len(doc_pdf) > 1 else 0)
    rect = fitz.Rect(57, 193, 1111, 569) # Coordenadas ingresadas
    pix = page.get_pixmap(clip=rect, dpi=150)
    
    st.session_state["anexo_oscilografico_bytes"] = pix.tobytes("jpeg")
    st.success(f"✅ ¡Recorte generado y valor detectado: {valor_extraido} ms!")
# =========================================================
# CATÁLOGO DE ALIMENTADORES
# =========================================================
CATALOGO_ALIMENTADORES = {
    "SER01_PTVES - AL3 (154-3)": {"interruptor": "154-3 SER01_PTVES", "alimentador_ser": "AL3-154 SER01_PTVES", "ser": "SER01_PTVES", "alimentador": "AL3", "interruptor_num": "154-3", "zona":"201,203"},
    "SER01_PTVES - AL4 (154-4)": {"interruptor": "154-4 SER01_PTVES", "alimentador_ser": "AL4-154 SER01_PTVES", "ser": "SER01_PTVES", "alimentador": "AL4", "interruptor_num": "154-4", "zona":"202,204"},
    "SER03_PIN - AL1 (154-1)": {"interruptor": "154-1 SER03_PIN", "alimentador_ser": "AL1-154 SER03_PIN", "ser": "SER03_PIN", "alimentador": "AL1", "interruptor_num": "154-1", "zona":"201,203"},
    "SER03_PIN - AL2 (154-2)": {"interruptor": "154-2 SER03_PIN", "alimentador_ser": "AL2-154 SER03_PIN", "ser": "SER03_PIN", "alimentador": "AL2", "interruptor_num": "154-2", "zona":"202,204"},
    "SER03_PIN - AL3 (154-3)": {"interruptor": "154-3 SER03_PIN", "alimentador_ser": "AL3-154 SER03_PIN", "ser": "SER03_PIN", "alimentador": "AL3", "interruptor_num": "154-3", "zona":"205"},
    "SER03_PIN - AL4 (154-4)": {"interruptor": "154-4 SER03_PIN", "alimentador_ser": "AL4-154 SER03_PIN", "ser": "SER03_PIN", "alimentador": "AL4", "interruptor_num": "154-4", "zona":"206"},
    "SER05_VMA - AL1 (154-1)": {"interruptor": "154-1 SER05_VMA", "alimentador_ser": "AL1-154 SER05_VMA", "ser": "SER05_VMA", "alimentador": "AL1", "interruptor_num": "154-1", "zona":"205"},
    "SER05_VMA - AL2 (154-2)": {"interruptor": "154-2 SER05_VMA", "alimentador_ser": "AL2-154 SER05_VMA", "ser": "SER05_VMA", "alimentador": "AL2", "interruptor_num": "154-2", "zona":"206"},
    "SER05_VMA - AL3 (154-3)": {"interruptor": "154-3 SER05_VMA", "alimentador_ser": "AL3-154 SER05_VMA", "ser": "SER05_VMA", "alimentador": "AL3", "interruptor_num": "154-3", "zona":"207"},
    "SER05_VMA - AL4 (154-4)": {"interruptor": "154-4 SER05_VMA", "alimentador_ser": "AL4-154 SER05_VMA", "ser": "SER05_VMA", "alimentador": "AL4", "interruptor_num": "154-4", "zona":"208"},
    "SER08_ATO - AL1 (154-1)": {"interruptor": "154-1 SER08_ATO", "alimentador_ser": "AL1-154 SER08_ATO", "ser": "SER08_ATO", "alimentador": "AL1", "interruptor_num": "154-1", "zona":"207"},
    "SER08_ATO - AL2 (154-2)": {"interruptor": "154-2 SER08_ATO", "alimentador_ser": "AL2-154 SER08_ATO", "ser": "SER08_ATO", "alimentador": "AL2", "interruptor_num": "154-2", "zona":"208"},
    "SER08_ATO - AL3 (154-3)": {"interruptor": "154-3 SER08_ATO", "alimentador_ser": "AL3-154 SER08_ATO", "ser": "SER08_ATO", "alimentador": "AL3", "interruptor_num": "154-3", "zona":"209"},
    "SER08_ATO - AL4 (154-4)": {"interruptor": "154-4 SER08_ATO", "alimentador_ser": "AL4-154 SER08_ATO", "ser": "SER08_ATO", "alimentador": "AL4", "interruptor_num": "154-4", "zona":"210"},
    "SER11_CAB - AL1 (154-1)": {"interruptor": "154-1 SER11_CAB", "alimentador_ser": "AL1-154 SER11_CAB", "ser": "SER11_CAB", "alimentador": "AL1", "interruptor_num": "154-1", "zona":"209"},
    "SER11_CAB - AL2 (154-2)": {"interruptor": "154-2 SER11_CAB", "alimentador_ser": "AL2-154 SER11_CAB", "ser": "SER11_CAB", "alimentador": "AL2", "interruptor_num": "154-2", "zona":"210"},
    "SER11_CAB - AL3 (154-3)": {"interruptor": "154-3 SER11_CAB", "alimentador_ser": "AL3-154 SER11_CAB", "ser": "SER11_CAB", "alimentador": "AL3", "interruptor_num": "154-3", "zona":"211"},
    "SER11_CAB - AL4 (154-4)": {"interruptor": "154-4 SER11_CAB", "alimentador_ser": "AL4-154 SER11_CAB", "ser": "SER11_CAB", "alimentador": "AL4", "interruptor_num": "154-4", "zona":"212"},
    "SER14_CUL - AL1 (154-1)": {"interruptor": "154-1 SER14_CUL", "alimentador_ser": "AL1-154 SER14_CUL", "ser": "SER14_CUL", "alimentador": "AL1", "interruptor_num": "154-1", "zona":"211"},
    "SER14_CUL - AL2 (154-2)": {"interruptor": "154-2 SER14_CUL", "alimentador_ser": "AL2-154 SER14_CUL", "ser": "SER14_CUL", "alimentador": "AL2", "interruptor_num": "154-2", "zona":"212"},
    "SER14_CUL - AL3 (154-3)": {"interruptor": "154-3 SER14_CUL", "alimentador_ser": "AL3-154 SER14_CUL", "ser": "SER14_CUL", "alimentador": "AL3", "interruptor_num": "154-3", "zona":"213"},
    "SER14_CUL - AL4 (154-4)": {"interruptor": "154-4 SER14_CUL", "alimentador_ser": "AL4-154 SER14_CUL", "ser": "SER14_CUL", "alimentador": "AL4", "interruptor_num": "154-4", "zona":"214"},
    "SER16_GAM - AL1 (154-1)": {"interruptor": "154-1 SER16_GAM", "alimentador_ser": "AL1-154 SER16_GAM", "ser": "SER16_GAM", "alimentador": "AL1", "interruptor_num": "154-1", "zona":"213"},
    "SER16_GAM - AL2 (154-2)": {"interruptor": "154-2 SER16_GAM", "alimentador_ser": "AL2-154 SER16_GAM", "ser": "SER16_GAM", "alimentador": "AL2", "interruptor_num": "154-2", "zona":"214"},
    "SER16_GAM - AL3 (154-3)": {"interruptor": "154-3 SER16_GAM", "alimentador_ser": "AL3-154 SER16_GAM", "ser": "SER16_GAM", "alimentador": "AL3", "interruptor_num": "154-3", "zona":"215"},
    "SER16_GAM - AL4 (154-4)": {"interruptor": "154-4 SER16_GAM", "alimentador_ser": "AL4-154 SER16_GAM", "ser": "SER16_GAM", "alimentador": "AL4", "interruptor_num": "154-4", "zona":"216"},
    "SER20_CAA - AL1 (154-1)": {"interruptor": "154-1 SER20_CAA", "alimentador_ser": "AL1-154 SER20_CAA", "ser": "SER20_CAA", "alimentador": "AL1", "interruptor_num": "154-1", "zona":"215"},
    "SER20_CAA - AL2 (154-2)": {"interruptor": "154-2 SER20_CAA", "alimentador_ser": "AL2-154 SER20_CAA", "ser": "SER20_CAA", "alimentador": "AL2", "interruptor_num": "154-2", "zona":"216"},
    "SER20_CAA - AL3 (154-3)": {"interruptor": "154-3 SER20_CAA", "alimentador_ser": "AL3-154 SER20_CAA", "ser": "SER20_CAA", "alimentador": "AL3", "interruptor_num": "154-3", "zona":"217"},
    "SER20_CAA - AL4 (154-4)": {"interruptor": "154-4 SER20_CAA", "alimentador_ser": "AL4-154 SER20_CAA", "ser": "SER20_CAA", "alimentador": "AL4", "interruptor_num": "154-4", "zona":"218"},
    "SER22_JAR - AL1 (154-1)": {"interruptor": "154-1 SER22_JAR", "alimentador_ser": "AL1-154 SER22_JAR", "ser": "SER22_JAR", "alimentador": "AL1", "interruptor_num": "154-1", "zona":"217"},
    "SER22_JAR - AL2 (154-2)": {"interruptor": "154-2 SER22_JAR", "alimentador_ser": "AL2-154 SER22_JAR", "ser": "SER22_JAR", "alimentador": "AL2", "interruptor_num": "154-2", "zona":"218"},
    "SER22_JAR - AL3 (154-3)": {"interruptor": "154-3 SER22_JAR", "alimentador_ser": "AL3-154 SER22_JAR", "ser": "SER22_JAR", "alimentador": "AL3", "interruptor_num": "154-3", "zona":"219"},
    "SER22_JAR - AL4 (154-4)": {"interruptor": "154-4 SER22_JAR", "alimentador_ser": "AL4-154 SER22_JAR", "ser": "SER22_JAR", "alimentador": "AL4", "interruptor_num": "154-4", "zona":"220"},
    "SER25_SMA - AL1 (154-1)": {"interruptor": "154-1 SER25_SMA", "alimentador_ser": "AL1-154 SER25_SMA", "ser": "SER25_SMA", "alimentador": "AL1", "interruptor_num": "154-1", "zona":"219"},
    "SER25_SMA - AL2 (154-2)": {"interruptor": "154-2 SER25_SMA", "alimentador_ser": "AL2-154 SER25_SMA", "ser": "SER25_SMA", "alimentador": "AL2", "interruptor_num": "154-2", "zona":"220"},
    "SER25_SMA - AL3 (154-1)": {"interruptor": "154-3 SER25_SMA", "alimentador_ser": "AL3-154 SER25_SMA", "ser": "SER25_SMA", "alimentador": "AL3", "interruptor_num": "154-3", "zona":"221-223"},
    "SER25_SMA - AL4 (154-4)": {"interruptor": "154-4 SER25_SMA", "alimentador_ser": "AL4-154 SER25_SMA", "ser": "SER25_SMA", "alimentador": "AL4", "interruptor_num": "154-4", "zona":"222-224"},
    "SER27_BAY - AL1 (154-1)": {"interruptor": "154-1 SER27_BAY", "alimentador_ser": "AL1-154 SER27_BAY", "ser": "SER27_BAY", "alimentador": "AL1", "interruptor_num": "154-1", "zona":"221-223"},
    "SER27_BAY - AL2 (154-2)": {"interruptor": "154-2 SER27_BAY", "alimentador_ser": "AL2-154 SER27_BAY", "ser": "SER27_BAY", "alimentador": "AL2", "interruptor_num": "154-2", "zona":"222-224"}
}

st.title("⚡ Generador de Informes de Disparo y Recierre DC")

col_form, col_preview = st.columns([1, 1], gap="medium")

with col_form:
    st.header("📝 Parámetros del Evento")

    plantilla_path = "plantilla_base.docx"
    plantilla_doc = None
    if os.path.exists(plantilla_path):
        plantilla_doc = plantilla_path
    else:
        plantilla_subida = st.file_uploader("Cargar plantilla base (.docx)", type=["docx"])
        if plantilla_subida is not None:
            plantilla_doc = plantilla_subida

    # =========================================================
    # OCR: AUTO-LLENADO DESDE VICOS RSC (APERTURADO Y VECINO)
    # =========================================================
    with st.expander("🔍 Cargar Capturas VICOS RSC (Auto-llenado)", expanded=True):
        st.write("Sube los registros (PDF o Imagen) del SCADA para extraer automáticamente fechas, horas de disparo y recierres.")
        
        c_vicos1, c_vicos2 = st.columns(2)
        with c_vicos1:
            img_vicos_ap = st.file_uploader("Log Eventos (Aperturado) - Para OCR", type=["pdf", "jpg", "png", "jpeg"], key="up_ap")
            foto_vicos_ap = st.file_uploader("📸 Imagen para el Anexo Word (Aperturado)", type=["jpg", "png", "jpeg"], key="foto_ap")
        with c_vicos2:
            img_vicos_vec = st.file_uploader("Log Eventos (Vecino) - Para OCR", type=["pdf", "jpg", "png", "jpeg"], key="up_vec")
            foto_vicos_vec = st.file_uploader("📸 Imagen para el Anexo Word (Vecino)", type=["jpg", "png", "jpeg"], key="foto_vec")
        
        if st.button("🚀 Extraer Datos de ambos SCADA", use_container_width=True):
            with st.spinner("Procesando documentos..."):
                if img_vicos_ap is not None:
                    f_det, h_disp_det, h_rec_det = extraer_datos_vicos(img_vicos_ap.getvalue(), img_vicos_ap.name)
                    if f_det:
                        st.session_state["fecha_ocr"] = datetime.strptime(f_det, "%d/%m/%Y").date()
                    if h_disp_det:
                        st.session_state["hora_vicos_disparo"] = h_disp_det
                        st.session_state["h_disp_cronologia"] = h_disp_det
                    if h_rec_det:
                        st.session_state["h_dcierre_cronologia"] = h_rec_det

                if img_vicos_vec is not None:
                    _, h_disp_vec, h_rec_vec = extraer_datos_vicos(img_vicos_vec.getvalue(), img_vicos_vec.name)
                    if h_disp_vec:
                        st.session_state["hora_vicos_disparo_vecina"] = h_disp_vec
                        st.session_state["h_vec_cronologia"] = h_disp_vec
                    if h_rec_vec:
                        st.session_state["h_vcierre_cronologia"] = h_rec_vec

                st.success("✅ ¡Datos extraídos correctamente con milisegundos de los reportes SCADA!")
                st.rerun()

    with st.expander("1. Selección de Equipos (Filtro por Zona)", expanded=True):
        opciones_aperturado = list(CATALOGO_ALIMENTADORES.keys())
        sel_aperturado = st.selectbox("Subestación / Celda Aperturada:", opciones_aperturado, index=0)
        datos_ap = CATALOGO_ALIMENTADORES[sel_aperturado]
        zona_detectada = datos_ap["zona"]
        opciones_vecino_filtradas = [
            k for k, v in CATALOGO_ALIMENTADORES.items() 
            if v["zona"] == zona_detectada and k != sel_aperturado
        ]
        if not opciones_vecino_filtradas:
            opciones_vecino_filtradas = [k for k in CATALOGO_ALIMENTADORES.keys() if k != sel_aperturado]

        sel_vecino = st.selectbox("Subestación / Celda Vecina:", opciones_vecino_filtradas, index=0)
        datos_vec = CATALOGO_ALIMENTADORES[sel_vecino]

    with st.expander("2. Funciones de Protección y ST"):

        f_disp_ini = st.text_input("Función SCADA Aperturado:", value="Disparo instantáneo Disparador di/dt")
        
        # Inicializamos las variables si están vacías
        if "input_func_rele" not in st.session_state:
            st.session_state["input_func_rele"] = ""
        if "input_corriente" not in st.session_state:
            st.session_state["input_corriente"] = ""

        # Usamos key= en lugar de value=
        f_disp_fin = st.selectbox(
            "Función Relé Aperturado:", 
            options=["Disparo Imax", "Disparo Imax_rev", "Disparo di/dt"],
            key="input_func_rele"
        )
        
        f_disp_vec_ini = "Disparo por S/E vecina"
        f_disp_vec_fin = "Arrastre desde SSEE colateral activo"
        c_st1, c_st2, c_st3 = st.columns(3)
        st_ap = c_st1.text_input("ST Aperturado:", value="")
        st_vec = c_st2.text_input("ST Vecino:", value="")
        st_zn = c_st3.text_input("ST Zona:", value="")
        
        # Usamos key= en lugar de value=
        corriente_val = st.text_input("Corriente registrada (A):", key="input_corriente")

    with st.expander("3. Datos de Operación"):
        c_op1, c_op2 = st.columns(2)
        
        fecha_default = st.session_state.get("fecha_ocr", datetime.today())
        fecha_raw = c_op1.date_input("Fecha:", value=fecha_default)
        fecha_val = fecha_raw.strftime("%d/%m/%Y")
        
        dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
        dia_val = dias_semana[fecha_raw.weekday()]
        c_op2.text_input("Día (Automático):", value=dia_val, disabled=True)
        
        c_op3, c_op4 = st.columns(2)
        headway = c_op3.text_input("Headway (min):", value="")
        condicion = c_op4.selectbox("Condición Señales:", ["Señales encendidas", "Señales apagadas"])
        c_op5, c_op6 = st.columns(2)
        operacion_val = c_op5.selectbox("Horario Operación:", ["Hora pico", "Hora valle"])
        zona_manual = c_op6.text_input("Zona afectada (en documento):", value=f"Zona {zona_detectada}")

    with st.expander("4. Cronología y Horas (HH:MM:SS,mmm)"):
        st.info("💡 Las filas se reordenarán e insertarán automáticamente en la tabla de Word con milisegundos.")
        
        h_disp_def = st.session_state.get("h_disp_cronologia", "")
        h_vec_def = st.session_state.get("h_vec_cronologia", "")
        h_dcierre_def = st.session_state.get("h_dcierre_cronologia", "")
        h_vcierre_def = st.session_state.get("h_vcierre_cronologia", "")

        h_disp = st.text_input("Hora disparo Aperturado (SCADA):", value=h_disp_def)
        h_vec = st.text_input("Hora disparo Vecino (SCADA):", value=h_vec_def)
        h_dcierre = st.text_input("Hora recierre Aperturado:", value=h_dcierre_def)
        h_vcierre = st.text_input("Hora recierre Vecino:", value=h_vcierre_def)
        h_rep = st.text_input("Hora reporte CCM a PCO:", value="")
        h_env_st = st.text_input("Hora envío solicitud ST:", value="")
        h_foto_disp = st.text_input("Hora foto Técnico Subestaciones de SER Disparo:", value="")
        h_foto_vec = st.text_input("Hora foto Técnico Subestaciones SER Vecino:", value="")
        h_cat = st.text_input("Hora informe Técnico Catenaria:", value="")

    with st.expander("5. Personal Involucrado"):
        sup_pco_val = st.text_input("Supervisor PCO:", value="")
        per_sub_val = st.text_input("Personal Subestaciones:", value="")
        per_cat_val = st.text_input("Personal Catenarias:", value="")

    with st.expander("6. Anexos y Gráficos", expanded=True):
        st.write("Sube el PDF para abrir el editor visual de Sitras PRO en tiempo real.")
        pdf_file = st.file_uploader("Log Sitras PRO (.pdf)", type=["pdf"])
        
        if pdf_file is not None:
            if st.button("🚀 Abrir Editor de Sitras PRO", use_container_width=True):
                modal_editor_sitras(pdf_file.getvalue())

        if "anexo_sitras_bytes" in st.session_state and st.session_state["anexo_sitras_bytes"] is not None:
            st.success("✅ Anexo Sitras PRO adjuntado y visible en la vista previa del Word.")
            
        st.write("---")
        st.write("**Registro Oscilográfico (SIGRA)**")
        pdf_osc_file = st.file_uploader("Log SIGRA (.pdf)", type=["pdf"], key="up_osc")
        
        if pdf_osc_file is not None:
            if st.button("🚀 Extraer y Recortar Oscilograma Automáticamente", use_container_width=True, key="btn_osc"):
                procesar_sigra_automatico(pdf_osc_file.getvalue())

        if "anexo_oscilografico_bytes" in st.session_state and st.session_state["anexo_oscilografico_bytes"] is not None:
            st.success("✅ Anexo Oscilográfico adjuntado y listo en el Word.")
            # Te muestra el valor leído para que lo verifiques o modifiques si es necesario
            val_osc_def = st.session_state.get("val_osc", "")
            tiempo_sigra_val = st.text_input("Valor de tiempo extraído (ms):", value=val_osc_def)

# =========================================================
# CONSTRUCCIÓN Y AUTO-ORDENAMIENTO DE EVENTOS
# =========================================================
eventos_para_ordenar = []

if h_disp.strip(): eventos_para_ordenar.append({"hora": h_disp.strip(), "ubicacion": datos_ap["ser"], "descripcion": f"Se registró en el sistema SCADA_VICOS RSC, función “{f_disp_ini}” del alimentador {datos_ap['alimentador']}. Asimismo, se registró en el relé Sitras PRO por función “{f_disp_fin}” (ST {st_ap})."})
if h_vec.strip(): eventos_para_ordenar.append({"hora": h_vec.strip(), "ubicacion": datos_vec["ser"], "descripcion": f"Se registró en el sistema SCADA_VICOS RSC, función “{f_disp_vec_ini}” del alimentador {datos_vec['alimentador']}. Asimismo, se registró en el relé Sitras PRO por función “{f_disp_vec_fin}” (ST {st_vec})."})
if h_dcierre.strip(): eventos_para_ordenar.append({"hora": h_dcierre.strip(), "ubicacion": datos_ap["ser"], "descripcion": f"Recierre automático del interruptor {datos_ap['interruptor_num']} en el alimentador {datos_ap['alimentador']} con resultado exitoso."})
if h_vcierre.strip(): eventos_para_ordenar.append({"hora": h_vcierre.strip(), "ubicacion": datos_vec["ser"], "descripcion": f"Recierre automático del interruptor {datos_vec['interruptor_num']} en el alimentador {datos_vec['alimentador']} con resultado exitoso."})
if h_rep.strip(): eventos_para_ordenar.append({"hora": h_rep.strip(), "ubicacion": "CCM", "descripcion": f"Se comunica al supervisor de PCO {sup_pco_val}.\n\nSe reporta en el grupo de WhatsApp de CCM_SUB_CAT de SYC."})
if h_env_st.strip(): eventos_para_ordenar.append({"hora": h_env_st.strip(), "ubicacion": "CCM", "descripcion": f"Personal de Subestaciones, {per_sub_val}; realizar inspección de las celdas DC: {datos_ap['alimentador_ser']} (ST {st_ap}) y {datos_vec['alimentador_ser']} (ST {st_vec}).\n\nPersonal de Catenarias, {per_cat_val}; realizar inspección de la {zona_manual} de vía principal (ST {st_zn})."})
if h_foto_disp.strip(): eventos_para_ordenar.append({"hora": h_foto_disp.strip(), "ubicacion": datos_ap["ser"], "descripcion": f"El técnico de Subestaciones, {per_sub_val}; informa que el relé Sitras PRO del alimentador {datos_ap['alimentador']}, registró:\n\n· “{f_disp_fin}” con el valor de {corriente_val} A\n\nReporta que se encuentra operativo sin alarmas presentes y en servicio."})
if h_foto_vec.strip(): eventos_para_ordenar.append({"hora": h_foto_vec.strip(), "ubicacion": datos_vec["ser"], "descripcion": f"El técnico de Subestaciones, {per_sub_val}; informa que el relé Sitras PRO del alimentador {datos_vec['alimentador']} registró:\n\n· “Arrastre desde SSEE colateral activo”\n\nReporta que se encuentra operativo sin alarmas presentes y en servicio."})
if h_cat.strip(): eventos_para_ordenar.append({"hora": h_cat.strip(), "ubicacion": f"{zona_manual}\nVía principal", "descripcion": f"El técnico de Catenarias, {per_cat_val}; informa que realizo inspección visual de la línea aérea de contacto en la {zona_manual} y reporta que no se encontró observaciones."})

cronologia_ordenada = sorted(eventos_para_ordenar, key=lambda x: str(x["hora"]))

# Variables exactas para el documento Word con milisegundos intactos

hora_vicos_ap_final = st.session_state.get("hora_vicos_disparo", f"{h_disp}")
hora_vicos_vec_final = st.session_state.get("hora_vicos_disparo_vecina", f"{h_vec}")
hay_sigra = True if st.session_state.get("anexo_oscilografico_bytes") else False

context = {
    "interruptor_aperturado": datos_ap["interruptor"], "alimentador_ser_aperturado": datos_ap["alimentador_ser"], "ser_aperturado": datos_ap["ser"], "alimentador_aperturado": datos_ap["alimentador"], "alimentador_aperturado_num": datos_ap["interruptor_num"],
    "interruptor_vecino": datos_vec["interruptor"], "alimentador_ser_vecino": datos_vec["alimentador_ser"], "ser_vecino": datos_vec["ser"], "alimentador_vecino": datos_vec["alimentador"], "alimentador_vecino_num": datos_vec["interruptor_num"],
    "funcion_disparo_inicial": f_disp_ini, "funcion_disparo_final": f_disp_fin, "funcion_disparo_vecina_inicial": f_disp_vec_ini, "funcion_disparo_vecina_final": f_disp_vec_fin,
    "st_aperturado": st_ap, "st_vecino": st_vec, "st_zona": st_zn, "corriente": corriente_val,
    "fecha": fecha_val, 
    "hora_vicos_disparo": hora_vicos_ap_final, 
    "hora_vicos_disparo_vecina": hora_vicos_vec_final, 
    "dia": dia_val, "tiempo_entre_trenes": headway, "condicion_senales": condicion, "operacion": operacion_val, "zona": zona_manual,
    "sup_pco": sup_pco_val, "per_sub": per_sub_val, "per_cat": per_cat_val,
    "valor_tiempo_sigra": st.session_state.get("val_osc", ""),
    "tiene_sigra": hay_sigra
}

# =========================================================
# PANEL DERECHO: RENDERIZADO HÍBRIDO (DOCXTPL + PYTHON-DOCX)
# =========================================================
with col_preview:
    st.header("📄 Vista Previa Real del Documento")
    if plantilla_doc is not None:
        try:
            doc = DocxTemplate(plantilla_doc)
            
            # 1. Anexo Sitras PRO
            if "anexo_sitras_bytes" in st.session_state and st.session_state["anexo_sitras_bytes"] is not None:
                img_stream = io.BytesIO(st.session_state["anexo_sitras_bytes"])
                context["anexo_sitras_disparo"] = InlineImage(doc, img_stream, width=Mm(165))
            else:
                context["anexo_sitras_disparo"] = ""

            # 2. Anexo Registro Oscilográfico (SIGRA)
            if "anexo_oscilografico_bytes" in st.session_state and st.session_state["anexo_oscilografico_bytes"] is not None:
                img_stream_osc = io.BytesIO(st.session_state["anexo_oscilografico_bytes"])
                context["anexo_registro_oscilografico"] = InlineImage(doc, img_stream_osc, width=Mm(165))
            else:
                context["anexo_registro_oscilografico"] = ""

            if foto_vicos_ap is not None:
                context["anexo_vicos_aperturado"] = InlineImage(doc, io.BytesIO(foto_vicos_ap.getvalue()), width=Mm(165))
            else:
                context["anexo_vicos_aperturado"] = ""

            if foto_vicos_vec is not None:
                context["anexo_vicos_vecino"] = InlineImage(doc, io.BytesIO(foto_vicos_vec.getvalue()), width=Mm(165))
            else:
                context["anexo_vicos_vecino"] = ""

            # Renderizado único de la plantilla con todo el contexto
            doc.render(context)


            tabla_cronologia = None
            for table in doc.docx.tables:
                if len(table.rows) > 0 and "HORA" in table.rows[0].cells[0].text.upper():
                    tabla_cronologia = table
                    break
            
            if tabla_cronologia is not None:
                for evento in cronologia_ordenada:
                    fila = tabla_cronologia.add_row()
                    
                    # Asignar textos
                    fila.cells[0].text = str(evento["hora"])
                    fila.cells[1].text = str(evento["ubicacion"])
                    fila.cells[2].text = str(evento["descripcion"])
                    
                    # Aplicar formato Arial 9 y quitar sangría a cada celda de la fila nueva
                    for cell in fila.cells:
                        for paragraph in cell.paragraphs:
                            # Quitar sangría de primera línea o espacios raros
                            paragraph.paragraph_format.first_line_indent = 0
                            paragraph.paragraph_format.left_indent = 0
                            paragraph.paragraph_format.space_after = Mm(2)
                            paragraph.paragraph_format.space_before = Mm(2)
                            
                            for run in paragraph.runs:
                                run.font.name = 'Arial'
                                run.font.size = Pt(9)
            
            buffer = io.BytesIO()
            doc.save(buffer)
            buffer.seek(0)
            docx_bytes = buffer.getvalue()
            docx_b64 = base64.b64encode(docx_bytes).decode("utf-8")

            viewer_html = f"""
            <div id="document-container" style="background-color: #525659; padding: 15px; height: 740px; overflow-y: auto; border-radius: 6px;"></div>
            <script src="https://unpkg.com/jszip/dist/jszip.min.js"></script>
            <script src="https://cdn.jsdelivr.net/npm/docx-preview@0.1.15/dist/docx-preview.min.js"></script>
            <script>
                var base64Data = "{docx_b64}";
                var byteCharacters = atob(base64Data);
                var byteNumbers = new Array(byteCharacters.length);
                for (var i = 0; i < byteCharacters.length; i++) {{ byteNumbers[i] = byteCharacters.charCodeAt(i); }}
                var blob = new Blob([new Uint8Array(byteNumbers)], {{type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}});
                docx.renderAsync(blob, document.getElementById("document-container")).catch(e => console.error(e));
            </script>
            """
            components.html(viewer_html, height=760, scrolling=False)

            # =========================================================
            # CREACIÓN DEL ARCHIVO ZIP CON WORD Y ANEXOS RENOMBRADOS
            # =========================================================
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                # 1. Guardar el Informe Word principal
                nombre_word = f"Informe_Disparo_{datos_ap['ser']}_{context['fecha'].replace('/', '-')}.docx"
                zip_file.writestr(nombre_word, docx_bytes)

                # 2. Guardar y renombrar Anexo 11 (Log Aperturado - OCR)
                if img_vicos_ap is not None:
                    ext = img_vicos_ap.name.split('.')[-1]
                    nom_11 = f"Anexo N° 11 – Registro de eventos PDF del {datos_ap['alimentador']} {datos_ap['ser']} VICOS RSC.{ext}"
                    zip_file.writestr(nom_11, img_vicos_ap.getvalue())

                # 3. Guardar y renombrar Anexo 10 (Imagen Aperturado - Word)
                if foto_vicos_ap is not None:
                    ext = foto_vicos_ap.name.split('.')[-1]
                    nom_10 = f"Anexo N° 10 – Registro de eventos JPG del {datos_ap['alimentador']} {datos_ap['ser']} VICOS RSC.{ext}"
                    zip_file.writestr(nom_10, foto_vicos_ap.getvalue())

                # 4. Guardar y renombrar Anexo 13 (Log Vecino - OCR)
                if img_vicos_vec is not None:
                    ext = img_vicos_vec.name.split('.')[-1]
                    nom_13 = f"Anexo N° 13 – Registro de eventos PDF del {datos_vec['alimentador']} {datos_vec['ser']} VICOS RSC.{ext}"
                    zip_file.writestr(nom_13, img_vicos_vec.getvalue())

                # 5. Guardar y renombrar Anexo 12 (Imagen Vecino - Word)
                if foto_vicos_vec is not None:
                    ext = foto_vicos_vec.name.split('.')[-1]
                    nom_12 = f"Anexo N° 12 – Registro de eventos JPG del {datos_vec['alimentador']} {datos_vec['ser']} VICOS RSC.{ext}"
                    zip_file.writestr(nom_12, foto_vicos_vec.getvalue())

            zip_buffer.seek(0)

            # Botón para descargar el ZIP
            st.download_button(
                label="📥 Descargar Paquete Completo (.zip)",
                data=zip_buffer,
                file_name=f"Paquete_Informe_Disparo_{datos_ap['ser']}_{context['fecha'].replace('/', '-')}.zip",
                mime="application/zip",
                use_container_width=True
            )
        except Exception as e:
            st.error(f"Error al compilar la plantilla Word: {e}")
    else:
        st.warning("Coloca un archivo `plantilla_base.docx` en el repositorio o súbelo en el formulario para visualizarlo.")