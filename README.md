# Valhalla Companion

Editor de inventario para **Assassin's Creed Valhalla** con una interfaz de Windows de 64 bits. El ejecutable funciona sin Cheat Engine ni Python instalado.

**Versión 0.4.0 · validación en partida pendiente.** Las 196 pruebas automatizadas usan datos simulados y procesos de prueba propios. Una firma detectada no garantiza compatibilidad con todas las versiones del juego. El alcance de las comprobaciones está en [docs/VALIDACION.md](docs/VALIDACION.md).

## Uso

Descarga y extrae el paquete portable de [la última versión en Releases](https://github.com/welstrec/AC-Valhalla/releases/latest). Conserva los avisos y licencias incluidos.

1. Abre Valhalla y carga una partida.
2. Ejecuta `ValhallaCompanion-0.4.0.exe`.
3. Pulsa **Buscar juego**, selecciona el proceso y pulsa **Adjuntar**.
4. Revisa **Compatibilidad** antes de usar una función.
5. Explora **Objetos y packs** o consulta **Mi inventario**.
6. Pulsa **Desconectar** para restaurar las ayudas temporales antes de cerrar.

Solo se aceptan los procesos `ACValhalla.exe` y `ACValhalla_plus.exe`. Si Windows deniega el acceso, abre la aplicación mediante **Ejecutar como administrador**.

## Funciones

- **Catálogo:** 2.327 objetos y 290 packs, organizados en Tienda y eventos, Juego base y Expansiones y extras. Incluye búsqueda, tipos de objeto, favoritos y vista del contenido de cada pack.
- **Lista e imágenes:** filas compactas con iconos, tarjetas opcionales y una ficha ampliable. Las ilustraciones originales interpretan nombres y categorías; no son imágenes oficiales ni confirman el aspecto real. Puedes importar imágenes locales PNG, JPEG o WebP.
- **Cantidades:** muestra la última lectura de la partida, su hora y un botón para actualizar. Distingue **Sin lectura**, **Sin registro** y un cero leído. No vigila continuamente la memoria.
- **Solicitudes:** permite preparar y revisar una lista antes de enviarla. Los identificadores no se duplican; al añadir uno de nuevo se sustituye su cantidad. Ante un fallo se detiene y no reintenta automáticamente las solicitudes ya intentadas.
- **Recursos ausentes («??»):** ofrece una reparación explícita de trece recursos básicos, con una unidad por recurso ausente y comprobación posterior. Excluye expansiones, eventos, desafíos y raciones; la ausencia de registro no se interpreta como cantidad cero.
- **Ayudas:** persistencia, caballo, bebida, Orlog, sigilo, multiplicador de plata y experiencia y solicitudes del mapa. Cada función tiene su propio diagnóstico. Las tiendas remotas y la edición del objeto equipado permanecen bloqueadas.

La interfaz admite teclado, foco visible, búsqueda sin distinción de acentos y tamaño de texto ajustable en **Ver**. `Ctrl+F` abre el buscador; `Ctrl++`, `Ctrl+-` y `Ctrl+0` cambian el tamaño del texto.

## Cantidades y compatibilidad

El equipo identificado se limita a una unidad y se omite si ya está presente, con una nueva lectura antes de solicitarlo. Esta es una protección para evitar copias, **no un máximo nativo descubierto en el juego**. Los packs mixtos mantienen una unidad por pieza de equipo y la cantidad elegida para los recursos. Los duplicados existentes se muestran tal como se leen y no se eliminan automáticamente.

Los recursos permiten valores como **9.999** y **100.000**, dentro del rango conservador de edición de **0 a 2.147.483.647**; las solicitudes empiezan en uno. Esto no demuestra que cada objeto admita ese rango dentro del juego. La tabla no expone los máximos internos de cada objeto: no se inventa un límite de 99 a partir de una cantidad actual de 99. Antes de enviar una solicitud se exige una lectura válida y se comprueba la suma de cantidad actual y solicitada. Las raciones no admiten edición directa.

Las cantidades y direcciones se vuelven a comprobar antes de escribir. Los parches temporales se verifican y restauran al desconectar; el ejecutable del juego en disco no se modifica. Los objetos o recompensas guardados en una partida pueden persistir después de desconectar. No se garantiza la ausencia de cierres. Si una llamada remota supera su tiempo de espera, la sesión se bloquea y es necesario reiniciar el juego antes de reconectar.

La tabla fuente se llama `1.6.2`, pero declara internamente juego `1.5.3`, tabla `1.1` y Cheat Engine `7.4`. La aplicación conserva esa discrepancia en el diagnóstico.

## Desarrollo y compilación

Requiere **Python 3.13 de 64 bits** en Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

Verificación sin conectar el juego:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe run.py --self-test
```

Para crear el EXE y los paquetes:

```powershell
.\build.ps1
```

La compilación ejecuta las pruebas, verifica el propio EXE y su icono de Windows y genera en `dist/` el ejecutable, el paquete portable, el ZIP de fuentes y `release.json` con tamaño, SHA-256 y alcance de validación. El ejecutable no tiene firma digital de editor. Las salidas generadas y el entorno local no forman parte del repositorio.

El catálogo se importa estáticamente desde `data/source.CT`: no se ejecutan Lua, Auto Assembler ni instrucciones del documento. Para regenerarlo conservando el nombre y los metadatos de procedencia originales:

```powershell
.\.venv\Scripts\python.exe tools\import_ct.py data\source.CT --source-name "AC Valhalla Inventory Editor - 1.6.2.CT"
```

## Datos locales y créditos

Favoritos e imágenes se guardan en `%LOCALAPPDATA%/ValhallaCompanion/library/`; los registros locales, en `%LOCALAPPDATA%/ValhallaCompanion/app.log`, con rotación. El diagnóstico se exporta manualmente. La aplicación no envía datos ni descarga actualizaciones.

La tabla original acredita a **budabum** y a sus colaboradores. Los créditos, la procedencia y las licencias de las dependencias están en [THIRD_PARTY.md](THIRD_PARTY.md) y [third_party/](third_party/). Las licencias de terceros se conservan por separado; este repositorio no les atribuye una licencia global nueva. El proyecto no está afiliado a Ubisoft ni a Cheat Engine.
