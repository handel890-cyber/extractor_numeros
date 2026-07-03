import os
import re
import cv2
import numpy as np
from PIL import Image
import pytesseract
import streamlit as st
import streamlit.components.v1 as components

# Configuración de página
st.set_page_config(page_title="Control de Trenes", layout="wide", page_icon="")
st.title("Verificador trenes")

# --- 1. CARPETA LOCAL PARA SERVIR LA IMAGEN ---
os.makedirs("frontend_componente", exist_ok=True)

# Inicializar estados de la sesión
if 'numeros_detectados' not in st.session_state:
    st.session_state.numeros_detectados = set()
if 'procesado' not in st.session_state:
    st.session_state.procesado = False

uploaded_file = st.file_uploader("Sube la imagen del reporte:", type=["png", "jpg", "jpeg"])

if uploaded_file:
    img_pil = Image.open(uploaded_file)
    
    # GUARDADO LOCAL INSTANTÁNEO
    img_path = "frontend_componente/bg.png"
    img_pil.save(img_path, format="PNG")
    
    # Dimensiones lógicas para pantalla
    display_width = 900
    scale = img_pil.width / display_width
    display_height = int(img_pil.height / scale)

    st.subheader("🖱️ Modo Dibujo: Clic a Clic")
    st.info("1️⃣ **Haz UN CLIC** donde inicia la esquina superior de tu zona.\n2️⃣ **Mueve el mouse** (verás un recuadro rojo punteado siguiéndote) y haz **OTRO CLIC** en la esquina opuesta para fijarlo en verde.\n❌ *Si te equivocas en el primer clic, presiona Clic Derecho o la tecla ESC para cancelar.*")

    # --- 2. CÓDIGO HTML/JS (SIN REINICIOS DE STREAMLIT) ---
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ margin: 0; padding: 0; background-color: transparent; font-family: sans-serif; }}
            #wrapper {{ position: relative; width: {display_width}px; height: {display_height}px; border: 1px solid #464855; overflow: hidden; user-select: none; background-color: #1a1a1a; }}
            #panzoom-layer {{ position: absolute; transform-origin: 0 0; width: {display_width}px; height: {display_height}px; }}
            .controles {{ margin-top: 15px; display: flex; gap: 15px; align-items: center; }}
            .btn-rojo {{ background-color: #ff4b4b; color: white; border: none; padding: 12px 24px; border-radius: 6px; cursor: pointer; font-weight: bold; font-size: 15px; box-shadow: 0px 4px 6px rgba(0,0,0,0.3); transition: 0.2s; }}
            .btn-rojo:hover {{ background-color: #ff3333; }}
            .btn-gris {{ background-color: #262730; color: white; border: 1px solid #464855; padding: 12px 24px; border-radius: 6px; cursor: pointer; font-weight: bold; font-size: 15px; }}
            #contador_zonas {{ color: #00FF00; font-weight: bold; font-size: 15px; margin-left: 10px; }}
        </style>
    </head>
    <body>
        <div id="wrapper">
            <div id="panzoom-layer">
                <img id="bg_img" src="" style="width: 100%; height: 100%; pointer-events: none; position: absolute; top:0; left:0;"/>
                <canvas id="canvas" width="{display_width}" height="{display_height}" style="position: absolute; top:0; left:0; cursor: crosshair;"></canvas>
            </div>
        </div>
        <div class="controles">
            <button id="btn_procesar" class="btn-rojo">🚀 PROCESAR SELECCIÓN AHORA</button>
            <button id="btn_borrar" class="btn-gris">🗑️ Borrar todo</button>
            <span id="contador_zonas">Zonas listas: 0</span>
        </div>

        <script>
            // Cargar imagen de inmediato sin caché en el navegador
            document.getElementById('bg_img').src = "bg.png?nocache=" + Math.random();

            const wrapper = document.getElementById('wrapper');
            const layer = document.getElementById('panzoom-layer');
            const canvas = document.getElementById('canvas');
            const ctx = canvas.getContext('2d');
            const contador = document.getElementById('contador_zonas');
            
            let rects = [];
            let isPanning = false;
            let zoom = 1; let panX = 0; let panY = 0;
            let startPanX = 0, startPanY = 0;
            
            // LÓGICA DE DIBUJO CLIC A CLIC
            let firstPoint = null;
            let currentMouseX = 0, currentMouseY = 0;

            function updateTransform() {{ layer.style.transform = `translate(${{panX}}px, ${{panY}}px) scale(${{zoom}})`; }}

            function drawAll() {{
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                
                // 1. Dibujar zonas verdes confirmadas
                rects.forEach((r, idx) => {{
                    ctx.strokeStyle = '#00FF00'; ctx.lineWidth = 3 / zoom;
                    ctx.strokeRect(r.x, r.y, r.w, r.h);
                    ctx.fillStyle = 'rgba(0, 255, 0, 0.15)'; ctx.fillRect(r.x, r.y, r.w, r.h);
                    ctx.fillStyle = '#00FF00'; ctx.font = `bold ${{Math.max(12, 14 / zoom)}}px sans-serif`;
                    ctx.fillText('Zona ' + (idx+1), r.x + (5/zoom), r.y + (18/zoom));
                }});
                
                // 2. Vista previa en vivo (caja punteada roja)
                if (firstPoint !== null) {{
                    ctx.strokeStyle = '#FF4B4B'; ctx.lineWidth = 2 / zoom;
                    ctx.setLineDash([6 / zoom, 6 / zoom]);
                    let w = currentMouseX - firstPoint.x;
                    let h = currentMouseY - firstPoint.y;
                    ctx.strokeRect(firstPoint.x, firstPoint.y, w, h);
                    ctx.setLineDash([]);
                    
                    // Punto rojo inicial
                    ctx.fillStyle = '#FF4B4B';
                    ctx.beginPath();
                    ctx.arc(firstPoint.x, firstPoint.y, 5 / zoom, 0, Math.PI * 2);
                    ctx.fill();
                }}
                contador.innerText = "Zonas listas: " + rects.length;
            }}

            wrapper.addEventListener('wheel', (e) => {{
                e.preventDefault();
                const rect = wrapper.getBoundingClientRect();
                const mouseX = e.clientX - rect.left; const mouseY = e.clientY - rect.top;
                const layerX = (mouseX - panX) / zoom; const layerY = (mouseY - panY) / zoom;
                if (e.deltaY < 0) {{ zoom *= 1.1; }} else {{ zoom /= 1.1; }}
                zoom = Math.min(Math.max(0.5, zoom), 8);
                panX = mouseX - layerX * zoom; panY = mouseY - layerY * zoom;
                updateTransform(); drawAll();
            }}, {{ passive: false }});

            wrapper.addEventListener('contextmenu', (e) => {{
                e.preventDefault();
                if (firstPoint !== null) {{ firstPoint = null; drawAll(); }}
            }});

            window.addEventListener('keydown', (e) => {{
                if (e.key === 'Escape' && firstPoint !== null) {{ firstPoint = null; drawAll(); }}
            }});

            wrapper.addEventListener('mousedown', (e) => {{
                const rect = canvas.getBoundingClientRect();
                const xClick = (e.clientX - rect.left) / zoom; const yClick = (e.clientY - rect.top) / zoom;
                
                if (e.button === 1 || e.shiftKey) {{
                    isPanning = true; startPanX = e.clientX - panX; startPanY = e.clientY - panY;
                    wrapper.style.cursor = 'move';
                }} else if (e.button === 0) {{
                    if (firstPoint === null) {{
                        firstPoint = {{ x: xClick, y: yClick }};
                        currentMouseX = xClick; currentMouseY = yClick;
                        drawAll();
                    }} else {{
                        let x = Math.min(firstPoint.x, xClick);
                        let y = Math.min(firstPoint.y, yClick);
                        let w = Math.abs(firstPoint.x - xClick);
                        let h = Math.abs(firstPoint.y - yClick);
                        if (w > 8 && h > 8) {{ rects.push({{x, y, w, h}}); }}
                        firstPoint = null;
                        drawAll();
                    }}
                }}
            }});

            wrapper.addEventListener('mousemove', (e) => {{
                const rect = canvas.getBoundingClientRect();
                if (isPanning) {{
                    panX = e.clientX - startPanX; panY = e.clientY - startPanY; updateTransform();
                }} else {{
                    currentMouseX = (e.clientX - rect.left) / zoom;
                    currentMouseY = (e.clientY - rect.top) / zoom;
                    if (firstPoint !== null) {{ drawAll(); }}
                }}
            }});

            window.addEventListener('mouseup', () => {{
                if (isPanning) {{ isPanning = false; wrapper.style.cursor = 'default'; }}
            }});

            document.getElementById('btn_borrar').addEventListener('click', () => {{
                rects = []; firstPoint = null; zoom = 1; panX = 0; panY = 0; updateTransform(); drawAll();
                window.parent.postMessage({{ isStreamlitMessage: true, type: "streamlit:setComponentValue", value: [] }}, "*");
            }});

            // ENVÍO DE DATOS A PYTHON
            document.getElementById('btn_procesar').addEventListener('click', () => {{
                if (rects.length === 0) {{ alert("Primero crea uno o más rectángulos verdes encima del reporte."); return; }}
                window.parent.postMessage({{ isStreamlitMessage: true, type: "streamlit:setComponentValue", value: rects }}, "*");
            }});
            
            window.addEventListener("load", () => {{
                window.parent.postMessage({{ isStreamlitMessage: true, type: "streamlit:componentReady", apiVersion: 1 }}, "*");
                window.parent.postMessage({{ isStreamlitMessage: true, type: "streamlit:setFrameHeight", height: {display_height} + 80 }}, "*");
            }});
        </script>
    </body>
    </html>
    """

    with open("frontend_componente/index.html", "w", encoding="utf-8") as f:
        f.write(html_content)

    # --- 3. INVOCAR COMPONENTE (CLAVE ESTABLE QUE SOLUCIONA EL REINICIO) ---
    componente_trenes = components.declare_component("componente_trenes", path="frontend_componente")
    # Al quitar la hora de la clave, Streamlit YA NO BORRA el recuadro ni reinicia la app
    coordenadas_recibidas = componente_trenes(key=f"visor_{uploaded_file.name}")

    # --- 4. CONSOLA DE ESTADO ---
    st.markdown("---")
    with st.expander("🔍 Estado del Sistema Veloz", expanded=True):
        if coordenadas_recibidas is None:
            st.warning("⏳ Imagen cargada en pantalla. Haz dos clics para delimitar tus zonas verdes...")
        elif isinstance(coordenadas_recibidas, list) and len(coordenadas_recibidas) > 0:
            st.success(f"⚡ ¡ÉXITO! Procesando {len(coordenadas_recibidas)} zonas con el Triple Motor Óptico...")
        else:
            st.info("No hay zonas seleccionadas actualmente.")

    # --- 5. TRIPLE MOTOR OCR VELOZ Y PRECISO ---
    if coordenadas_recibidas and isinstance(coordenadas_recibidas, list) and len(coordenadas_recibidas) > 0:
        st.session_state.numeros_detectados = set()
        
        cv_img_full = np.array(img_pil.convert('L'))

        for idx, r in enumerate(coordenadas_recibidas):
            x1 = max(0, int(r['x'] * scale))
            y1 = max(0, int(r['y'] * scale))
            x2 = min(img_pil.width, int((r['x'] + r['w']) * scale))
            y2 = min(img_pil.height, int((r['y'] + r['h']) * scale))

            if x2 - x1 < 5 or y2 - y1 < 5: continue

            # 1. Recorte y agrandado 2.5X Cúbico (alta nitidez)
            crop_gray = cv_img_full[y1:y2, x1:x2]
            h, w = crop_gray.shape
            crop_zoom = cv2.resize(crop_gray, (int(w * 2.5), int(h * 2.5)), interpolation=cv2.INTER_CUBIC)

            # 2. Filtro Otsu Thresholding
            if np.mean(crop_zoom) < 127:
                _, thresh = cv2.threshold(crop_zoom, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            else:
                _, thresh = cv2.threshold(crop_zoom, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            # 3. TRIPLE ESCANEO EN PARALELO PARA MÁXIMA PRECISIÓN
            whitelist = '-c tessedit_char_whitelist=0123456789'
            
            # Motor 1: Bloque de tabla (PSM 6 sobre Otsu)
            t1 = pytesseract.image_to_string(thresh, config=f'--psm 6 {whitelist}')
            # Motor 2: Texto disperso para números rebeldes (PSM 11 sobre Otsu)
            t2 = pytesseract.image_to_string(thresh, config=f'--psm 11 {whitelist}')
            # Motor 3: Escala de grises directa (por si Otsu borró un número fino como el 1 o el 7)
            t3 = pytesseract.image_to_string(crop_zoom, config=f'--psm 6 {whitelist}')

            encontrados = re.findall(r'\b\d+\b', t1 + " " + t2 + " " + t3)

            for n_str in set(encontrados):
                try:
                    n = int(n_str)
                    if 1 <= n <= 44:
                        st.session_state.numeros_detectados.add(n)
                except ValueError:
                    pass
                    
        st.session_state.procesado = True

    # --- 6. MOSTRAR RESULTADOS ---
    st.header("📊 Resultados Consolidados del 1 al 44")
    
    if st.session_state.procesado:
        todos = set(range(1, 45))
        faltantes = sorted(list(todos - st.session_state.numeros_detectados))
        
        col1, col2 = st.columns(2)
        with col1:
            st.success(f"✅ **Detectados ({len(st.session_state.numeros_detectados)}):**")
            st.write(sorted(list(st.session_state.numeros_detectados)))
        with col2:
            if faltantes:
                st.error(f"❌ **Faltan {len(faltantes)} números:**")
                st.write(faltantes)
            else:
                st.success("🎉 ¡Felicidades! Todos los números del 1 al 44 están mapeados completos.")
    else:
        st.info("💡 Haz clic en una esquina y luego en la opuesta para crear tus recuadros. Luego presiona **'🚀 PROCESAR SELECCIÓN AHORA'**.")