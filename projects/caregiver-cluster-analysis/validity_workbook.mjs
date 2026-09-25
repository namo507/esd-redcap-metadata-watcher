/** Artifact Tool authoring only. Analytical decisions are supplied by Python. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';

const [payloadPath, outputPath, previewDirectory] = process.argv.slice(2);
if (!payloadPath || !outputPath) throw new Error('Expected payload and output paths');
const payload = JSON.parse(await fs.readFile(payloadPath, 'utf8'));
if (payload.sheets.length !== 16) throw new Error('Exactly 16 analytical tables are required');
const workbook = Workbook.create();
const col = index => {let s = ''; for (index++; index > 0; index = Math.floor((index-1)/26)) s = String.fromCharCode(65+(index-1)%26)+s; return s;};
for (const spec of payload.sheets) {
  const sheet = workbook.worksheets.add(spec.name);
  const first = spec.headerRow;
  const last = first + spec.rows.length;
  const end = col(spec.headers.length-1);
  sheet.showGridLines = false;
  sheet.tabColor = [10,11,13,14].includes(spec.number) ? '#234E70' : '#A6B3BF';
  sheet.getRange('A2').values = [[spec.title]];
  sheet.getRange('A2').format.font = {name:'Arial', size:14, bold:true, color:'#234E70'};
  sheet.getRange('A3').values = [[`${spec.rows.length.toLocaleString('en-US')} table rows${spec.note ? '. ' + spec.note : ''}`]];
  sheet.getRange('A3').format.font = {name:'Arial',size:10,color:'#52616D',italic:true};
  const grid = sheet.getRange(`A${first}:${end}${last}`);
  grid.values = [spec.headers,...spec.rows];
  grid.format.font = {name:'Arial',size:10,color:'#253545'};
  grid.format.wrapText = true;
  grid.format.verticalAlignment = 'top';
  grid.format.rowHeight = 36;
  for (let c=0;c<spec.headers.length;c++) {
    const range = sheet.getRange(`${col(c)}${first}:${col(c)}${Math.max(first,last)}`);
    range.format.columnWidth = spec.widths[c];
    if (last > first && spec.formats[c]) sheet.getRange(`${col(c)}${first+1}:${col(c)}${last}`).setNumberFormat(spec.formats[c]);
  }
  const header = sheet.getRange(`A${first}:${end}${first}`);
  header.format = {fill:'#234E70',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},
    wrapText:true,rowHeight:60,verticalAlignment:'center',horizontalAlignment:'center'};
  const table = sheet.tables.add(`A${first}:${end}${Math.max(last,first+1)}`,true,`ValidityTable${spec.number}`);
  table.style = 'TableStyleMedium2';
  table.showFilterButton = true;
  sheet.freezePanes.freezeRows(first);
  const pidIndex = spec.headers.indexOf('Study PID');
  const nameIndex = spec.headers.indexOf('Study name');
  if (pidIndex >= 0 && nameIndex >= 0 && Math.max(pidIndex,nameIndex) < 3) sheet.freezePanes.freezeColumns(Math.max(pidIndex,nameIndex)+1);
  // Fit the required text without compressing the font. Wide evidence fields
  // remain complete; Excel's row-height maximum is never silently exceeded.
  for (let r=0;r<spec.rows.length;r++) {
    let lines=1;
    for (let c=0;c<spec.headers.length;c++) {
      const value = spec.rows[r][c];
      if (value === null) continue;
      const width = Math.max(8,spec.widths[c]-2);
      lines = Math.max(lines,...String(value).split('\n').map(line=>Math.ceil(line.length/width)));
    }
    sheet.getRange(`A${first+r+1}:${end}${first+r+1}`).format.rowHeight = Math.min(409,Math.max(32,lines*14+8));
  }
  for (let c=0;c<spec.headers.length;c++) {
    if (last <= first) continue;
    const values = spec.rows.map(row=>row[c]);
    if (!values.some(value=>typeof value === 'string' && spec.classifications[value])) continue;
    const range = sheet.getRange(`${col(c)}${first+1}:${col(c)}${last}`);
    for (const [label,fill] of Object.entries(spec.classifications)) range.conditionalFormats.add('cellIs',{
      operator:'equal',formula:`"${label}"`,format:{fill,font:{color:'#253545'}}});
    for (let r=0;r<values.length;r++) {
      const fill = spec.classifications[values[r]];
      if (fill) sheet.getRange(`${col(c)}${first+r+1}`).format.fill = fill;
    }
  }
  for (let r=0;r<spec.rows.length;r++) {
    if (spec.rows[r].slice(0,3).some(value=>['Total','Grand total','All studies'].includes(value))) {
      sheet.getRange(`A${first+r+1}:${end}${first+r+1}`).format.font.bold = true;
    }
  }
}
workbook.recalculate();
// Compare source values with the actual Artifact Tool cells before export.
let cellsChecked=0;
for (const spec of payload.sheets) {
  const actual = workbook.worksheets.getItem(spec.name).getRange(`A${spec.headerRow}:${col(spec.headers.length-1)}${spec.headerRow+spec.rows.length}`).values;
  const expected = [spec.headers,...spec.rows];
  for (let r=0;r<expected.length;r++) for (let c=0;c<expected[r].length;c++) {
    if ((actual[r][c] ?? '') !== (expected[r][c] ?? '')) throw new Error(`Cell content mismatch in table ${spec.number}`);
    cellsChecked++;
  }
}
if (previewDirectory) {
  await fs.mkdir(previewDirectory,{recursive:true});
  for (const spec of payload.sheets) {
    const blob=await workbook.render({sheetName:spec.name,range:`A1:${col(Math.min(7,spec.headers.length-1))}${Math.min(spec.headerRow+spec.rows.length,spec.headerRow+5)}`,scale:1,format:'png'});
    await fs.writeFile(path.join(previewDirectory,`${String(spec.number).padStart(2,'0')}.png`),new Uint8Array(await blob.arrayBuffer()));
  }
}
await fs.mkdir(path.dirname(outputPath),{recursive:true});
await (await SpreadsheetFile.exportXlsx(workbook)).save(outputPath);
console.log(JSON.stringify({sheets:16,cells_checked:cellsChecked,recalculated:true}));
