/** Native spreadsheet author for the refined, source-validated caregiver export.
 * Run through generate_all_responses_excel.py so the runtime dependencies are
 * resolved without adding authoring packages to the research project.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const [payloadPath, outputPath, previewDirectory] = process.argv.slice(2);
if (!payloadPath || !outputPath) throw new Error('Expected payload and output paths');
const payload = JSON.parse(await fs.readFile(payloadPath, 'utf8'));
const workbook = Workbook.create();
const col = n => { let s = ''; for (n++; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + (n - 1) % 26) + s; return s; };
const value = v => v && typeof v === 'object' && v.excelDate ? new Date(v.excelDate) : v;
for (const spec of payload.sheets) workbook.worksheets.add(spec.name);
for (const [index, spec] of payload.sheets.entries()) {
  const sheet = workbook.worksheets.getItem(spec.name);
  const first = spec.headerRow || 1;
  const last = first + spec.rows.length;
  const end = col(spec.headers.length - 1);
  sheet.showGridLines = false;
  sheet.tabColor = index < 6 ? '#234E70' : '#A6B3BF';
  if (spec.title) {
    sheet.getRange('A2').values = [[spec.title]];
    sheet.getRange('A2').format.font = { name: 'Arial', size: 15, bold: true, color: '#234E70' };
  }
  const grid = sheet.getRange(`A${first}:${end}${Math.max(last, first)}`);
  grid.values = [spec.headers, ...spec.rows.map(row => row.map(value))];
  grid.format.font = { name: 'Arial', size: 10, color: '#243746' };
  grid.format.rowHeight = spec.name === 'Rule Audit' ? 30 : spec.name === 'Rule Definitions' ? 72 : 42;
  grid.format.verticalAlignment = 'center';
  const header = sheet.getRange(`A${first}:${end}${first}`);
  header.format = { fill: '#234E70', font: { name: 'Arial', size: 10, bold: true, color: '#FFFFFF' }, wrapText: true, rowHeight: 45, verticalAlignment: 'center', horizontalAlignment: 'center' };
  for (let c = 0; c < spec.headers.length; c++) {
    sheet.getRange(`${col(c)}${first}:${col(c)}${Math.max(last, first)}`).format.columnWidth = spec.widths?.[c] || 18;
    if (spec.formats?.[c] && last > first) sheet.getRange(`${col(c)}${first + 1}:${col(c)}${last}`).setNumberFormat(spec.formats[c]);
    if (last > first && (spec.headers[c] === 'Study' || /Why this action|Review plan|Exclusion reason|Definition|description|notes|Live REDCap project title|^Check$|field$/i.test(spec.headers[c]))) sheet.getRange(`${col(c)}${first + 1}:${col(c)}${last}`).format.wrapText = true;
  }
  if (spec.rows.length) {
    const table = sheet.tables.add(`A${first}:${end}${last}`, true, `CaregiverTable${index + 1}`);
    table.style = 'TableStyleMedium2';
    table.showFilterButton = true;
  }
  if (spec.name !== 'Summary') {
    sheet.freezePanes.freezeRows(first);
    sheet.freezePanes.freezeColumns(Math.min(3, spec.headers.length));
  }
  for (const [cell, formula] of Object.entries(spec.formulas || {})) sheet.getRange(cell).formulas = [[formula]];
  if (last > first) {
    for (const [c, label] of spec.headers.entries()) {
      const range = sheet.getRange(`${col(c)}${first + 1}:${col(c)}${last}`);
      if (label === 'Recommended action' || label === 'Action from Excel') {
        const fills = {'Pay now':'#C6EFCE','Check by hand':'#FFEB9C','Do not pay':'#FFC7CE','Incomplete - not eligible':'#D9D9D9'};
        for (const [text, fill] of Object.entries(fills)) range.conditionalFormats.add('cellIs', {operator:'equal',formula:`"${text}"`,format:{fill}});
        for (let row = 0; row < spec.rows.length; row++) {
          const fill = fills[spec.rows[row][c]];
          if (fill) sheet.getRange(`${col(c)}${first + row + 1}`).format.fill = fill;
        }
      }
      if (/parity|Audit result/i.test(label)) range.conditionalFormats.add('cellIs', {operator:'equal',formula:'"ERROR"',format:{fill:'#FFC7CE',font:{color:'#9C0006',bold:true}}});
      if (/^R\d+ result$/.test(label)) range.conditionalFormats.add('cellIs', {operator:'equal',formula:'"FLAG"',format:{fill:'#FFEB9C'}});
    }
  }
  for (const note of spec.notes || []) {
    sheet.getRange(note.cell).values = [[note.text]];
    sheet.getRange(note.cell).format.font = {name:'Arial',size:10,color:'#516675',italic:true};
  }
}
workbook.recalculate();
// Check actual formula values before export. Native LibreOffice recalculation is
// also verified by the Python wrapper; cached PASS text alone is insufficient.
let checked = 0;
for (const spec of payload.sheets) {
  const sheet = workbook.worksheets.getItem(spec.name);
  for (const [cell, expected] of Object.entries(spec.expected || {})) {
    const actual = sheet.getRange(cell).values[0][0];
    if (actual !== expected && !(typeof expected === 'number' && Math.abs(actual - expected) < 1e-9)) throw new Error(`Formula mismatch in ${spec.name}!${cell}: expected ${expected}, received ${actual}`);
    checked++;
  }
}
if (previewDirectory) {
  await fs.mkdir(previewDirectory, {recursive:true});
  for (const [index, spec] of payload.sheets.entries()) {
    const first = spec.headerRow || 1;
    const end = col(Math.min(spec.headers.length - 1, spec.name === 'Summary' ? 9 : 4));
    const preview = await workbook.render({sheetName:spec.name,range:`A1:${end}${Math.min(first + spec.rows.length, first + 7)}`,scale:1,format:'png'});
    await fs.writeFile(path.join(previewDirectory, `${String(index+1).padStart(2,'0')}.png`),new Uint8Array(await preview.arrayBuffer()));
  }
}
await fs.mkdir(path.dirname(outputPath), {recursive:true});
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
await fs.writeFile(`${outputPath}.authoring.json`, JSON.stringify({sheets:payload.sheets.length,formula_checks:checked,engine:'artifact-tool',recalculated:true}));
console.log(JSON.stringify({sheets:payload.sheets.length,formula_checks:checked,recalculated:true}));
