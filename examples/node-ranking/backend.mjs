// A deliberately different full policy: substring matching, favorites, then alphabetical order.
import readline from 'node:readline';
const input = readline.createInterface({ input: process.stdin });
const send = (id, result) => process.stdout.write(`${JSON.stringify({ jsonrpc: '2.0', id, result })}\n`);
const fold = text => text.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase();

for await (const line of input) {
  const message = JSON.parse(line);
  const p = message.params || {};
  if (message.method === 'shutdown') break;
  if (message.method === 'cancel') continue;
  if (message.method === 'initialize') {
    if (p.api_version !== 2 || p.wire_version !== 1) throw new Error('Unsupported protocol');
    send(message.id, { api_version: 2, wire_version: 1, plugin_id: p.plugin_id, has_handler: false });
  } else if (message.method === 'rank') {
    if (p.protocol_version !== 1) throw new Error('Unsupported ranking protocol');
    const query = fold(p.query.trim());
    const matches = p.candidates.filter(row =>
      [row.title, row.subtitle, row.keywords, p.aliases[row.id] || ''].some(text => fold(text).includes(query)));
    const favorite = id => p.favorites.includes(id) ? p.favorites.indexOf(id) : p.favorites.length;
    matches.sort((a, b) => favorite(a.id) - favorite(b.id) || a.title.localeCompare(b.title) || a.id.localeCompare(b.id));
    send(message.id, { catalog_revision: p.catalog_revision, ordered_ids: matches.slice(0, p.limit).map(row => row.id) });
  }
}
