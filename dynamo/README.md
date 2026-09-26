# Generar Encofrado (Dynamo)

Script de Dynamo, independiente de esta extension de pyRevit, para generar
encofrado automatico sobre los elementos estructurales del modelo activo
(cimentaciones, muros, columnas, vigas y losas), con **espesor de 2"**.

Por cada cara plana libre de cada elemento crea un panel `DirectShape`
(categoria Modelos Genericos) extruido 2" hacia afuera. Detecta caras en
contacto con un elemento vecino (ej. una viga que llega a una columna) y
las recorta para no duplicar espesor de encofrado en las juntas. Por
defecto tambien omite la cara superior (superficie de vaciado) y la cara
inferior de cimentaciones (apoyada en el terreno/solado).

## Archivos

- `GenerarEncofrado_2in.dyn` - grafo listo para abrir en Dynamo: un nodo
  Python Script (motor CPython3) conectado a un nodo Watch.
- `GenerarEncofrado_2in.py` - el mismo codigo del nodo Python, como
  referencia / respaldo por si el `.dyn` no abre igual en tu version de
  Dynamo.

## Uso

1. Abre el modelo en Revit y lanza Dynamo (version 2.x/3.x, motor CPython3).
2. Abre `GenerarEncofrado_2in.dyn`.
3. Corre el grafo (modo manual: boton "Run"). El nodo Watch muestra un
   resumen: paneles creados, areas por categoria (m2) y avisos.

Si el `.dyn` no carga correctamente (puede variar segun version de
Dynamo/Revit), crea un nodo "Python Script" en blanco y pega el contenido
de `GenerarEncofrado_2in.py`.

## Configuracion

Todo se ajusta editando la seccion `CONFIGURATION` al inicio del script
(dentro del nodo Python):

- `PANEL_THICKNESS_IN` - espesor del encofrado en pulgadas (por defecto `2.0`).
- `CONTACT_TOLERANCE_MM` - tolerancia para considerar dos elementos "en contacto".
- `EXCLUDE_TOP_FACES` - omitir la cara superior (superficie de vaciado).
- `EXCLUDE_FOUNDATION_BOTTOM` - omitir la cara inferior de cimentaciones.
- `CATEGORIES` - subconjunto de `["foundations","walls","columns","beams","slabs"]`.
- `CREATE_GEOMETRY` - `False` para solo calcular metrado sin crear paneles.

## Notas

- El script es autocontenido: no depende de pyRevit ni del paquete
  `revit_mcp` de este repo (usa `RevitServices` para el documento activo y
  la transaccion, disponibles siempre en Dynamo for Revit).
- No crea ni enlaza parametros compartidos (a diferencia de la
  herramienta MCP `generate_formwork` de este repo, que si usa
  `EF_Area_m2`, `EF_Categoria_Origen`, etc.). En su lugar, cada panel
  guarda el origen y el area en el parametro "Comentarios".
- Compatible con Revit 2022-2027+ (maneja tanto `ElementId.Value` como
  `ElementId.IntegerValue`, y evita la ambiguedad de `ElementId(int)` en
  Revit 2027+).
