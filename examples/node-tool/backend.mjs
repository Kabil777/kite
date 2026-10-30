// Standalone API-v2 backend: Python/Qt owns all rendering and actions.
import readline from 'node:readline';

const send = message => process.stdout.write(`${JSON.stringify({ jsonrpc: '2.0', ...message })}\n`);
const view = data => ({ kind: 'view', data });
const input = readline.createInterface({ input: process.stdin });
let pluginId;

for await (const line of input) {
  const message = JSON.parse(line);
  const p = message.params || {};
  if (message.method === 'shutdown') break;
  if (message.method === 'cancel') continue; // Work here is synchronous and tiny.
  let result;
  if (message.method === 'initialize') {
    if (p.api_version !== 2 || p.wire_version !== 1) throw new Error('Unsupported protocol');
    pluginId = p.plugin_id;
    result = { api_version: 2, wire_version: 1, plugin_id: pluginId, has_handler: true };
  } else if (message.method === 'search') {
    result = view({
      results: [{ id: 'greeting', title: p.query || 'Open greeting form', keywords: p.query,
        action: { type: 'activate', value: 'form' } }],
      catalog: { placeholder: 'Type a greeting', preserve_order: true },
    });
  } else if (message.method === 'handle_event' && p.event.node_id === 'greeting') {
    result = view({ nodes: [{ type: 'form', id: 'greeting-form',
      props: { title: 'Greeting', submit_text: 'Done' }, events: { submit: {} },
      children: [{ type: 'text_input', id: 'name', props: { placeholder: 'Your name' } }],
    }] });
  } else if (message.method === 'handle_event') {
    result = { kind: 'toast', data: { title: `Hello, ${p.event.payload.inputs?.name || 'friend'}`, tone: 'success' } };
  } else {
    send({ id: message.id, error: { code: -32601, message: 'Unknown method' } });
    continue;
  }
  send({ id: message.id, result });
}
