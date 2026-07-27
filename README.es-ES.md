# Video Automation

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-required-orange.svg)](https://ffmpeg.org/)

**中文说明：[README.zh-CN.md](README.zh-CN.md)**

Convierte una grabación local larga en un video corto revisado con transcripción, subtítulos, imágenes de portada y archivos de exportación. Todo se ejecuta en tu computadora a menos que elijas un servicio de IA externo.

![Video Automation dashboard](docs/assets/dashboard.png)

> Video Automation acepta archivos de video locales. La descarga de URLs y la grabación de transmisiones en vivo no están incluidas. Los archivos originales nunca se modifican, y el inicio de sesión en plataformas o la publicación automática están desactivados por defecto.

## Inicio rápido en 5 minutos

### Aplicación para Windows (recomendado)

1. Descarga el último paquete de Windows desde [GitHub Releases](https://github.com/1076184145/video-automation/releases).
2. Instálalo o descomprímelo, luego ejecuta `VideoAutomationLite.exe`.
3. Abre **Health**. Si faltan FFmpeg o FFprobe, haz clic en **Auto-fix Dependencies**.
4. Abre **New Job** y añade un video local.
5. Elige un perfil como **Fast**, **Douyin** o **Bilibili**, y comienza el procesamiento.
6. Abre el trabajo terminado, revísalo y descarga `final.mp4`.

El flujo de trabajo básico no requiere una clave de API.

### Ejecutar desde el código fuente

Requisitos:

- Python 3.11 o posterior
- FFmpeg y FFprobe disponibles en el `PATH`
- Git
- Opcional: una GPU NVIDIA para transcripción y renderizado más rápidos

Windows PowerShell:

```powershell
git clone https://github.com/1076184145/video-automation.git
cd video-automation
py -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements-transcription-faster.txt
.\venv\Scripts\python.exe .\run_worker.py --serve
```

macOS o Linux:

```bash
git clone https://github.com/1076184145/video-automation.git
cd video-automation
python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements-transcription-faster.txt
./venv/bin/python run_worker.py --serve
```

El comando recomendado instala el entorno de ejecución Faster-Whisper, más ligero, utilizado por la configuración predeterminada. `requirements.txt` mantiene el respaldo compatible con OpenAI Whisper CLI. FunASR con respaldo de Faster-Whisper utiliza `requirements-transcription-funasr.txt`; el empaquetado de escritorio, Pillow, Demucs y todos los demás extras permanecen en `requirements-optional.txt`.

Crea un entorno virtual de Linux separado dentro de WSL. Un `venv` de Windows bajo `D:\` no puede ser reutilizado por el Python de WSL.

Abre [http://127.0.0.1:8765/#/](http://127.0.0.1:8765/#/) en tu navegador. Mantén la ventana de la terminal abierta mientras uses la aplicación.

## Flujo de trabajo diario

1. **Importar:** arrastra un video local o selecciónalo desde `input/recordings`.
2. **Elegir:** selecciona un perfil y activa solo las opciones que necesites.
3. **Procesar:** la aplicación inspecciona el video, transcribe el habla, sugiere cortes y renderiza las salidas seleccionadas.
4. **Revisar:** previsualiza los clips, edita los cortes o el texto de la transcripción, y vuelve a ejecutarlo si es necesario.
5. **Exportar:** descarga el video final, los subtítulos, la portada o el paquete de publicación manual.

Los perfiles son puntos de partida:

| Perfil | Úsalo para |
|---|---|
| **Fast** | Un video final rápido con menos análisis opcional |
| **Analysis** | Resultados de transcripción y detección sin una exportación completa |
| **Douyin** | Salida de video corto vertical |
| **Bilibili** | Salida estándar orientada a Bilibili |
| **YouTube Shorts** | Salida vertical para Shorts |

## Características

Incluido en el flujo de trabajo local:

- Importación de video individual y por lotes
- Progreso del trabajo, recuperación al reiniciar y una cola de tareas persistente
- Progreso consciente de la etapa, reintentos duraderos y controles cooperativos de cancelación/eliminación
- Transcripción de voz con backends locales compatibles con Whisper
- Comprobaciones de silencio, congelamiento, escenas y cuadros dañados
- Cortes sugeridos, edición de transcripciones, subtítulos delimitados por líneas y un editor de clips con desplazamiento horizontal
- Refinamiento de límites de clip local delimitado que evita palabras parciales sin aumentar la cobertura de medios inválidos
- Vista previa en el navegador más `final.mp4` de calidad completa
- Salida vertical `1080x1920` e incrustación de subtítulos (burn-in)
- Proyectos, recetas reutilizables, configuraciones de creador y revisiones de revisión
- Archivos de traspaso para Premiere Pro y Jianying/CapCut
- Paquetes de subida manual para plataformas compatibles

Características opcionales:

- Portadas por IA, traducción, títulos, descripciones y sugerencias de momentos destacados
- Aceleración NVIDIA CUDA/NVENC
- Transcripción local Faster-Whisper (principal `medium`, respaldo `small` por defecto); se siguen soportando las configuraciones heredadas de FunASR
- Separación de audio con Demucs
- Un conector de publicación configurado por separado; los paquetes manuales siguen siendo el respaldo

Las funciones de IA requieren una clave del proveedor que selecciones. Las claves ingresadas en **Settings** se almacenan en el almacén de credenciales del sistema operativo; el archivo `.env` privado contiene solo una referencia. Las claves existentes en texto plano en `.env` pueden migrarse desde la advertencia que se muestra en **Settings**. Consulta [`.env.example`](.env.example) para ver las configuraciones disponibles.

La transcripción se ejecuta en un proceso aislado con latidos de fase (heartbeats), un tiempo de espera por falta de progreso, limpieza del árbol de procesos y un disyuntor de backend temporal. Por lo tanto, un intento de modelo fallido avanza hacia el respaldo configurado en lugar de bloquear la cola durante la duración completa del tiempo de espera derivado.

## Salidas importantes

Cada trabajo se almacena en `processing/jobs/<job-name>/`.

| Archivo | Qué es |
|---|---|
| `final.mp4` | Video final de calidad completa |
| `web_preview.mp4` | Vista previa del navegador más pequeña |
| `transcript.txt` / `.srt` | Transcripción y subtítulos |
| `cuts.json` | Rangos de clips sugeridos o editados |
| `clip_refinement.json` | Intentos de comprobación de límites deterministas, puntuaciones y estado de recuperación |
| `cover_*.jpg` | Portadas generadas o seleccionadas |
| `publish_packages/` | Archivos y texto para subida manual |
| `project_exports/` | Archivos de traspaso para Premiere Pro o Jianying/CapCut |

## Solución de problemas

**La aplicación dice que falta FFmpeg o FFprobe**

Abre **Health** y usa **Auto-fix Dependencies**. Los usuarios de código fuente pueden ejecutar:

```powershell
.\venv\Scripts\python.exe .\run_worker.py --health
```

**El primer trabajo es lento**

Los modelos de voz pueden descargarse e inicializarse en el primer uso. Los trabajos posteriores reutilizan los archivos de modelo locales y pueden comenzar más rápido.

**Un trabajo en ejecución no se detiene inmediatamente después de hacer clic en Cancelar**

La cancelación es cooperativa: el trabajo primero cambia a **Canceling** mientras se termina el subproceso de transcripción o renderizado activo y se liberan sus recursos. Si la aplicación fue interrumpida, reiníciala y utiliza la acción de recuperación o eliminación que se muestra para el trabajo obsoleto.

**Falla el procesamiento con CUDA o GPU**

Elige un modelo de voz más pequeño o cambia la transcripción/renderizado a CPU en **Settings**.

**Un botón de IA informa que falta una clave**

El flujo de trabajo de edición local sigue funcionando. Configura la clave de un proveedor solo si deseas esa función de IA.

**¿Dónde están mis trabajos?**

Abre `processing/jobs/`. No envíes esta carpeta, `.env`, logs, videos privados o exportaciones generadas a Git.

## Comandos para desarrolladores

```powershell
# Mostrar todas las opciones de la CLI
.\venv\Scripts\python.exe .\run_worker.py --help

# Comprobación de salud legible por máquina
.\venv\Scripts\python.exe .\run_worker.py --health --json

# Procesar un archivo local
.\venv\Scripts\python.exe .\run_worker.py --once "D:\path\video.mp4" --profile douyin --progress

# Vista previa de limpieza de trabajos completados antiguos sin borrar nada
.\venv\Scripts\python.exe .\run_worker.py --cleanup-days 30 --cleanup-mode intermediates --dry-run

# Recuperar solo cachés de audio y archivos temporales de trabajos completados
.\venv\Scripts\python.exe .\run_worker.py --cleanup-days 30 --cleanup-mode intermediates

# Ejecutar pruebas de Python
.\venv\Scripts\python.exe -m unittest discover -s tests
```

El servidor Web local se vincula a `127.0.0.1:8765` por defecto. Los vínculos que no sean de loopback son rechazados a menos que `API_ALLOW_REMOTE=true` esté configurado explícitamente. Esa bandera no es una autenticación: el uso remoto sigue requiriendo un firewall, un proxy inverso autenticado y HTTPS. La guía de contribución está en [CONTRIBUTING.md](CONTRIBUTING.md).

## Privacidad y Límites

- Los videos, trabajos y claves de proveedores permanecen en tu computadora por defecto.
- Las funciones de IA externas envían solo la solicitud y la credencial requeridas directamente al proveedor que elijas.
- El proyecto no opera un servidor intermediario ni un servicio de auto-actualización remoto.
- Los paquetes de publicación manual no inician sesión ni suben archivos automáticamente.
- Eres responsable de los derechos para procesar y publicar tus medios.

Informa los problemas de seguridad de forma privada como se describe en [SECURITY.md](SECURITY.md).

## Licencia

Video Automation está disponible bajo la [MIT License](LICENSE). Las herramientas, modelos, fuentes y APIs de terceros pueden tener términos separados; consulta [NOTICE](NOTICE).
