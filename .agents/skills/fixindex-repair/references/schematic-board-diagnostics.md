# Reading a Schematic for Board-Level Diagnosis

Use after `references/schematic-acquisition.md` has produced the PDF set, when
the question is "which IC / which pin / which net is responsible".

## Procedure

1. Extract the **embedded text layer** of every PDF part with pypdf before
   trying to look at pages:
   `PdfReader(f).pages[i].extract_text()`; save one page per marker such as
   `===PAGE n===` so a keyword hit can be mapped back to a page number.
2. Grep the dumped text for subsystem terms (`audio`, `codec`, `SPK`, `mic`,
   `HPH`, `VREG`, `PMIC`, `reset`, `clock`, `I2C`) and collect the matching page
   numbers. Read only those pages first.
3. The text layer of a schematic normally contains the full component
   designators (`U####`, `J####`, `TP####`), pin names with ball numbers, net
   labels with cross-page references (`<59>`), and passive values. Use those
   directly; do not retype from a screenshot.
4. Render the shortlisted pages with pymupdf
   (`page.get_pixmap(matrix=Matrix(2.5, 2.5))`) for visual confirmation when
   image analysis is available.
5. Trace the functional chain in order — source (SoC) → converter/codec →
   driver/amplifier → switch/connector/load — and write down the exact part
   numbers and pin names at each stage.
6. Map the symptom to the stage: output-only loss points at the driver/amp and
   its supplies; loss of every input and output points at the codec or its
   rails/reset/clock; input-only loss points at the sensor/mic front-end and
   bias; a single path failing points at the switch or connector.
7. Confirm each IC's role from its datasheet with a web search before naming
   it as the suspect. State only voltages, resistances and pin functions that
   appear in the schematic or a datasheet you actually read.
8. Report test points by their schematic designators (`TP59xx`, `TP60xx`,
   etc.). If the text layer does not reveal which net a pad belongs to, say
   that a boardview/placement diagram is needed to map it rather than guessing.

## Pitfalls

- A timeout or failure in image analysis is a rendering limitation, not absent
  data: fall back to the PDF text layer, which usually carries every designator
  and net label. Do not tell the user to inspect the schematic by hand while
  that text has not been extracted.
- Recognize an IC by its pin signature as well as its part number: a class-D
  speaker amp shows `VBAT`/`VDDD`/`VDDP`/`VBST`/`OUTP`/`OUTN` plus I2C and
  reset; a codec shows DAC/ADC, `SLIMBUS`/`I2S`, mic-bias and headset-detect
  pins. A part number alone is not enough to assign a role.
- Trust the PDF reader's page count over `file`/metadata output, which can be
  stale on linearized PDFs.
- Do not transfer another device's failure modes, diode values or test points
  into the answer just because the models are related; see the device-scoping
  rule in SKILL.md.
