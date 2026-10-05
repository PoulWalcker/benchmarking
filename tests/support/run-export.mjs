// Narrow test driver for generated Code/Merge graphs. This is NOT n8n.
// Executes trusted generated JavaScript with a minimal $input adapter.
import fs from 'node:fs';
const artifact = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const override = process.argv[3] ? JSON.parse(fs.readFileSync(process.argv[3], 'utf8')) : null;
const nodes = new Map(artifact.nodes.map(n => [n.name, n]));
const incoming = new Map([...nodes.keys()].map(name => [name, []]));
for (const [from, outputs] of Object.entries(artifact.connections)) {
  for (const output of outputs.main) for (const edge of output) incoming.get(edge.node).push({from, port: edge.index});
}
const results = new Map();
const pending = new Set(nodes.keys());
while (pending.size) {
  const ready = [...pending].filter(name => incoming.get(name).every(x => results.has(x.from)));
  if (!ready.length) throw new Error('Deadlock/cycle in exported graph');
  for (const name of ready) {
    const node = nodes.get(name);
    const inputs = incoming.get(name).sort((a,b) => a.port-b.port).flatMap(x => results.get(x.from));
    let output;
    if (node.type.endsWith('.manualTrigger')) output = [{json:{}}];
    else if (node.type.endsWith('.merge')) output = inputs;
    else if (node.type.endsWith('.code')) {
      const execute = new Function('$input', node.parameters.jsCode);
      output = execute({all: () => JSON.parse(JSON.stringify(inputs))});
    } else throw new Error('Unsupported test-driver node: ' + node.type);
    if (name === 'Fixture' && override) output[0].json.inputs = override;
    results.set(name, output);
    pending.delete(name);
  }
}
process.stdout.write(JSON.stringify(results.get('Result')[0].json));

