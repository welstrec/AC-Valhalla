**Procedencia y créditos**

La tabla de referencia es `AC Valhalla Inventory Editor - 1.6.2.CT`, SHA-256 `43589F128DC160C034E39834E1BBCBD2F98D832B0D1E6201E9283554916A6905`. Se conserva una copia íntegra en `data/source.CT`. Los comentarios originales permiten compartir la tabla manteniendo sus créditos. Esa indicación no se interpreta aquí como una licencia general de otras obras o dependencias.

Autor original acreditado: **budabum**.

Colaboradores acreditados en el documento: SunBeam, madicos, Load, pedrokuk, AndyReloads, machine4578, Wolfin2010, WT3WD, Kriztalix, HiroLine, ibsarp, Jag345, Vodziak, Avike, N3M3515, Bruce Lee, Ninomae Tako'nis, sasha788999, SteampunkJolt, XXRexy, dna, sierax, Trillogy, kaporalzer, Sergant744, DDos96, acsokis, weedemup7, aSwedishMagyar, DarkByte, panraven y HK26.

Referencias de la tabla:

- [Comunidad y tabla original](https://fearlessrevolution.com/viewtopic.php?f=4&t=14379).
- [Código fuente de Cheat Engine](https://github.com/cheat-engine/cheat-engine).

La aplicación reimplementa funciones específicas mediante WinAPI y trabajadores finitos; no distribuye ni necesita el programa Cheat Engine. El proyecto EU5 del usuario sirvió como referencia de flujo y organización; no se reutilizaron sus offsets del juego.

Dependencias utilizadas para construir esta entrega:

- Python, [licencia y código fuente](https://docs.python.org/3/license.html).
- Qt for Python / PySide6 y Shiboken6, [código y licencias](https://code.qt.io/pyside/pyside-setup.git/).
- Keystone Engine, [código y licencia](https://github.com/keystone-engine/keystone).
- Capstone Engine, [código y licencia](https://github.com/capstone-engine/capstone).
- PyInstaller, [licencia y excepción para ejecutables](https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt).
- Pillow, utilizado para normalizar las imágenes locales importadas y generar el icono de la aplicación, [código y licencia](https://github.com/python-pillow/Pillow).
- QtAwesome 1.4.2, iconos de los controles de la interfaz, [código y licencia MIT](https://github.com/spyder-ide/qtawesome/tree/v1.4.2).
- QtPy 2.4.3, adaptación entre QtAwesome y PySide6, [código y licencia MIT](https://github.com/spyder-ide/qtpy/tree/v2.4.3).

El paquete portable incluye los textos de licencia disponibles en las distribuciones instaladas de sus dependencias. Los componentes dinámicos conservan sus licencias. El código nuevo de la aplicación se entrega al usuario dentro de este proyecto.

Los controles utilizan **Font Awesome 5 Solid**. El paquete estándar de QtAwesome incluye además las fuentes Font Awesome Free 5.15.4 y 6.7.2, Elusive Icons 2.0, Material Design Icons 5.9.55 y 6.9.96, Phosphor 1.3.0, Remix Icon 2.5.0 y Codicons 0.0.36. Se conservan sin modificar. Sus licencias son, respectivamente, SIL OFL 1.1 para Font Awesome y Elusive; Apache 2.0 para las fuentes Material Design y Remix 2.5.0; MIT para Phosphor; y CC BY 4.0 para Codicons. Los autores, declaraciones originales, textos completos y procedencia están en `third_party/qtawesome-fonts/NOTICE.txt` y sus archivos adyacentes; la distribución portable los copia a `licenses/qtawesome-fonts/`. La licencia de Remix se refiere expresamente a la versión 2.5.0 incluida.

Las ilustraciones semánticas del catálogo son dibujos vectoriales originales creados para este proyecto, conservados en `assets/semantic_icons/` y generados mediante `valhalla/icons.py`. Interpretan nombres y categorías; no se presentan como el aspecto real de los objetos. No se han extraído texturas, capturas ni iconos del juego, ni se ha incorporado arte de otros proyectos. Las imágenes que el usuario importe se guardan en su biblioteca local y no forman parte de la distribución.
