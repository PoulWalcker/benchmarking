// Test double for `n8n execute`: runs a stub-mode compiled graph's Code and
// Merge nodes in dependency order and prints the runData n8n would persist,
// with per-node sources, timing and the first node error. This is NOT n8n;
// it lets tests produce a complete recorded case without the engine.
import fs from 'node:fs';
const artifact = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const nodes = new Map(artifact.nodes.map(n => [n.name, n]));
const incoming = new Map([...nodes.keys()].map(name => [name, []]));
for (const [from, outputs] of Object.entries(artifact.connections)) {
  outputs.main.forEach((output, port) => {
    for (const edge of output) incoming.get(edge.node).push({from, port, index: edge.index});
  });
}
const AsyncFunction = (async () => {}).constructor;
const outputs = new Map();
const runData = {};
let clock = 1000;
let error = null;
const pending = new Set(nodes.keys());
while (pending.size && !error) {
  const ready = [...pending].filter(name => incoming.get(name).every(x => outputs.has(x.from)));
  if (!ready.length) throw new Error('Deadlock/cycle in exported graph');
  for (const name of ready.sort()) {
    const node = nodes.get(name);
    const parents = incoming.get(name).sort((a, b) => a.index - b.index);
    const inputs = parents.flatMap(x => outputs.get(x.from));
    const record = {
      startTime: clock,
      executionTime: 1,
      source: parents.length ? parents.map(x => ({previousNode: x.from, previousNodeOutput: x.port, previousNodeRun: 0})) : null,
    };
    clock += 10;
    try {
      let items;
      if (node.type.endsWith('.manualTrigger')) items = [{json: {}}];
      else if (node.type.endsWith('.merge')) items = inputs;
      else if (node.type.endsWith('.code')) {
        const execute = new AsyncFunction('$input', '$env', node.parameters.jsCode);
        items = await execute({all: () => JSON.parse(JSON.stringify(inputs))}, process.env);
      } else throw new Error('Unsupported test-double node: ' + node.type);
      record.data = {main: [items]};
      outputs.set(name, items);
    } catch (thrown) {
      record.error = {message: thrown.message, name: 'NodeOperationError'};
      error = {message: thrown.message, node: {name}};
    }
    runData[name] = [record];
    pending.delete(name);
    if (error) break;
  }
}
process.stdout.write(JSON.stringify({runData, error}));
