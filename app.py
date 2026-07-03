import streamlit as st
from PIL import Image, ImageDraw
from streamlit_cropper import st_cropper
import pytesseract
import re
import uuid

# Configuración de página
st.set_page_config(page_title="Control de Trenes Multi-Zona", layout="wide", page_icon="🎛️")
st.title("🎛️ Verificador de Trenes Multi-Zona (1-44)")

# --- Inicialización del Estado de la Sesión ---
if 'recortes_guardados' not in st.session_state:
    st.session_state.recortes_guardados = [] # Lista para coordenadas
if 'id_imagen' not in st.session_state:
    st.session_state.id_imagen = ""
if 'numeros_detectados' not in st.session_state:
    st.session_state.numeros_detectados = set()

# Función para limpiar recortes al subir nueva imagen
def clear_crops():
    st.session_state.recortes_guardados = []
    st.session_state.numeros_detectados = set()

# --- Subida de Imagen ---
uploaded_file = st.file_uploader(
    "Sube la imagen del reporte (Salida de Trenes):",
    type=["png", "jpg", "jpeg"],
    on_change=clear_crops
)

if uploaded_file:
    # Asegurar id de imagen único para resetear
    img_uid = str(uuid.uuid4())
    if st.session_state.id_imagen != uploaded_file.name + img_uid:
        st.session_state.id_imagen = uploaded_file.name + img_uid

    img = Image.open(uploaded_file)
    width, height = img.size

    # Crear una copia de la imagen para dibujar los marcadores de recortes guardados
    img_con_marcadores = img.copy()
    draw = ImageDraw.Draw(img_con_marcadores)
    
    # Dibujar rectángulos para los recortes ya guardados
    for i, crop_coords in enumerate(st.session_state.recortes_guardados):
        # Dibujar marcador (ej. un rectángulo rojo translúcido)
        # crop_coords es (left, top, right, bottom)
        left, top, right, bottom = crop_coords
        draw.rectangle([left, top, right, bottom], outline="red", width=5)
        # Añadir número de recorte
        draw.text((left + 10, top + 10), f"#{i+1}", fill="white", font=None)

    col_crop, col_stats = st.columns([2, 1])

    with col_crop:
        st.subheader("✂️ Dibuja y Guarda tus Zonas de Recorte")
        
        if st.session_state.recortes_guardados:
            st.info(f"Se han guardado **{len(st.session_state.recortes_guardados)}** zonas para procesar. Mueve el cuadro para añadir otra.")
        else:
            st.warning("Usa el recuadro para seleccionar una zona (ej. columna TREN de un patio) y presiona 'Añadir Recorte a la Lista'.")

        # Visualizar la imagen con marcadores y el cropper activo
        # st_cropper devuelve las coordenadas en un formato específico (dict)
        # pero también la imagen recortada. Necesitamos las coordenadas para la lista.
        
        # Obtenemos las coordenadas del cropper actual
        # Usamos key para forzar re-renderizado
        
        # Definimos una función de retorno para capturar las coordenadas
        if 'cropper_box' not in st.session_state:
            st.session_state.cropper_box = (0, 0, 0, 0)

        # Usamos st_cropper para dibujar, y capturamos las coordenadas directamente
        # a través de los valores devueltos en la imagen recortada (como atributos)
        cropped_instance = st_cropper(img_con_marcadores, realtime_update=True, box_color='#FF4B4B', aspect_ratio=None, key=st.session_state.id_imagen)
        
        # Botones de control de recortes
        st.write("")
        c1, c2, c3 = st.columns(3)
        
        with c1:
            if st.button("➕ Añadir Recorte a la Lista"):
                # Obtenemos las coordenadas de la instancia del recortador (cropper_box)
                # que es accesible a través del componente.
                # NOTA: st_cropper devuelve un objeto de imagen PIL modificado.
                # Sus coordenadas están en cropped_instance.crop_info
                try:
                    # Acceder a la información de recorte interna
                    crop_info = cropped_instance.crop_info
                    left = crop_info['left']
                    top = crop_info['top']
                    right = left + crop_info['width']
                    bottom = top + crop_info['height']
                    
                    if (left, top, right, bottom) not in st.session_state.recortes_guardados:
                        st.session_state.recortes_guardados.append((left, top, right, bottom))
                        st.success(f"Recorte #{len(st.session_state.recortes_guardados)} guardado.")
                        st.rerun() # Actualizar visualización
                    else:
                        st.warning("Esa zona ya está guardada.")
                except AttributeError:
                    st.error("No se pudieron capturar las coordenadas del recorte.")

        with c2:
            if st.button("🗑️ Limpiar Lista de Recortes", type="secondary"):
                clear_crops()
                st.rerun()
                
        with c3:
            if st.button("🔍 Procesar Todos los Recortes", type="primary"):
                if not st.session_state.recortes_guardados:
                    st.error("Por favor, guarda al menos un recorte en la lista.")
                else:
                    # Ejecutar OCR en cada zona guardada
                    st.session_state.numeros_detectados = set() # Reiniciar
                    progreso = st.progress(0)
                    for i, (left, top, right, bottom) in enumerate(st.session_state.recortes_guardados):
                        # Recortar la imagen original
                        crop_img_final = img.crop((left, top, right, bottom))
                        
                        # OCR
                        texto_extraido = pytesseract.image_to_string(crop_img_final, config='--psm 6')
                        numeros_encontrados = re.findall(r'\b\d+\b', texto_extraido)
                        
                        for num_str in numeros_encontrados:
                            num = int(num_str)
                            if 1 <= num <= 44:
                                st.session_state.numeros_detectados.add(num)
                        
                        progreso.progress((i + 1) / len(st.session_state.recortes_guardados))
                    
                    st.success("¡Todas las zonas procesadas!")
                    st.rerun()

    # --- Mostrar Resultados Consolidados ---
    st.markdown("---")
    st.header("📊 Resultados Consolidados de Todas las Zonas")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("✅ Números Detectados:")
        lista_detectados = sorted(list(st.session_state.numeros_detectados))
        st.write(lista_detectados if lista_detectados else "Ninguno detectado aún.")
        
    with col2:
        st.subheader("❌ Números Faltantes (del 1 al 44):")
        # Calcular cuáles faltan
        todos_los_numeros = set(range(1, 45))
        faltantes = sorted(list(todos_los_numeros - st.session_state.numeros_detectados))
        
        if st.session_state.numeros_detectados: # Solo mostrar faltantes si ya se procesaron datos
            if faltantes:
                st.error(f"Faltan {len(faltantes)} números:")
                st.write(faltantes)
            else:
                st.success("🎉 ¡Están todos los números completos del 1 al 44!")
        else:
            st.write("Procesa la lista de recortes para ver resultados.")

# Instrucciones de uso visibles siempre
st.sidebar.title("Instrucciones")
st.sidebar.markdown("""
1. **Sube** la imagen del reporte.
2. Usando el recuadro rojo, **dibuja** la primera zona (ej. columna TREN VES).
3. Presiona **'Añadir Recorte a la Lista'**. Verás un rectángulo rojo permanente en la zona guardada.
4. **Mueve** el cuadro rojo a la segunda zona (ej. columna TREN BAY) y presiona **'Añadir Recorte...'** otra vez.
5. Repite para todas las zonas que desees (las verás numeradas: #1, #2...).
6. Presiona el botón verde **'Procesar Todos los Recortes'** para consolidar los números y ver cuáles faltan del 1 al 44.
""")