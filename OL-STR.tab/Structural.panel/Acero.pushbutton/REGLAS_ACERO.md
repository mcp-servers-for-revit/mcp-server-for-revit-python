# Acero - Columnas: reglas de colocación de estribos

Reglas acordadas con el usuario (proceso constructivo en obra). El plugin las
aplica siempre; las pruebas de `tests/unit/test_rebar_spec.py` (clase
`TestStirrups`) fallan si alguna se rompe. **No cambiarlas sin su visto bueno.**

## 1. Luz libre (dónde se distribuyen los estribos)

- **Abajo:** desde la base de la columna (cara superior de la losa, viga o
  zapata inferior).
- **Arriba:** hasta el **fondo de la viga de mayor peralte** que llega a la
  columna. Si llegan vigas de distinto peralte, se toma siempre la más
  peraltada. Si no llega ninguna viga, hasta la **cara inferior de la losa**.
- El fondo se mide con la geometría real de la viga o losa **donde toca la
  columna**, no con su caja envolvente.
- **Nudo:** lo que queda por encima (peralte de la viga o espesor de la losa),
  con estribos al espaciamiento de "Estribo en núcleo".

Código: `revit_mcp/rebar_columns.py` → `clear_top`.

## 2. Distribución, ejemplo `1@0.05, 5@0.10, rto@0.20`

Todo se mide **desde cada extremo hacia el centro**; arriba es el espejo de
abajo (contando desde el fondo de la viga o losa).

| Tramo | En Revit | Posición (desde cada extremo) |
|---|---|---|
| `1@0.05` | 1 estribo, regla **Individual** | a 0.05 |
| `5@0.10` | 1 conjunto **Número con espaciado**, cantidad 5, espaciado 0.10 | 0.15 … 0.55 (contando desde el de 0.05) |
| `rto@0.20` | 1 conjunto **Número con espaciado** a 0.20 **por extremo** | desde el último de `5@0.10`: 0.75, 0.95 … hasta el centro |

- **Cada tramo y cada extremo es su propio conjunto de barras**, en orden:
  1@0.05 abajo, 5@0.10 abajo, resto desde abajo, resto desde arriba,
  5@0.10 arriba, 1@0.05 arriba, y el nudo.
- **El sobrante queda en el centro**, entre los dos restos. Si pasa del
  espaciado del resto (0.20), se agrega un estribo al medio. Si los dos
  últimos casi coinciden (menos de 1 cm), queda uno solo.
- En una columna corta, las zonas se cortan en el centro.

Código: `revit_mcp/rebar_spec.py` → `stirrup_sets`.

## 3. Varios estribos a la misma altura

- Cuando el dibujo tiene más de un estribo o grapa (por ejemplo dos M_T1
  traslapados), se colocan **juntos pero sin solaparse**: cada uno un
  diámetro de estribo por encima del anterior. Orden: borde, confinamiento y
  grapas.
- En la mitad superior se apilan **hacia abajo** (hacia el centro), para que
  el primero conserve su 0.05 desde la viga.

Código: `revit_mcp/rebar_spec.py` → `stack_lifts`; `rebar_columns.py` → `_runs`.

## 4. Barras longitudinales

- Se amarran siempre al estribo: con la herramienta **Barras**, un clic a
  menos de 6 cm de un estribo cerrado la pega a su cara interior (en la
  esquina si está cerca de una).
- Se alinean frente a frente con las barras ya colocadas (misma x o y a
  menos de 3 cm).
- La forma **M_00** (recta sin ganchos) es la de las barras longitudinales,
  no la de las grapas.

Código: `revit_mcp/rebar_spec.py` → `place_bar`.

## 5. Pesos

- El metrado usa el **peso nominal** de
  `revit_mcp/data/acero_pesos_por_diametro.csv`.
- Los tipos de barra llevan los parámetros **DIAMETRO DE BARRA** y
  **PESO NOMINAL (kg/m)**, que se llenan automáticamente.
