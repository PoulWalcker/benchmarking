// Local Code/Merge/IF probe only. Native evidence comes from real n8n separately.
import fs from 'node:fs';
const graph = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const nodes = new Map(graph.nodes.map(n => [n.name, n]));
const incoming = new Map([...nodes.keys()].map(name => [name, []]));
for (const [source, outputs] of Object.entries(graph.connections))
  outputs.main.forEach((edges, channel) => edges.forEach(e => incoming.get(e.node).push({source, channel, port:e.index})));
const outputs = new Map(), executed = [], pending = new Set(nodes.keys());
let error = null;
try {
  while (pending.size) {
    const ready = [...pending].filter(name => incoming.get(name).every(e => outputs.has(e.source)));
    if (!ready.length) throw new Error('Unresolved dependencies');
    for (const name of ready) {
      const node = nodes.get(name);
      const input = incoming.get(name).sort((a,b)=>a.port-b.port).flatMap(e => outputs.get(e.source)[e.channel] || []);
      let channels = [[]];
      if (node.type.endsWith('.manualTrigger')) channels = [[{json:{}}]];
      else if (input.length) {
        executed.push(name);
        if (node.type.endsWith('.merge')) channels = [input];
        else if (node.type.endsWith('.if')) {
          const field = node.parameters.conditions.conditions[0].leftValue.match(/\$json\.(\w+)/)[1];
          channels = [[], []];
          channels[input[0].json[field] === true ? 0 : 1] = input;
        } else if (node.type.endsWith('.code')) {
          const isolated = JSON.parse(JSON.stringify(input));
          channels = [new Function('$input', node.parameters.jsCode)({all:()=>isolated})];
        } else throw new Error('Unsupported probe node ' + node.type);
      }
      outputs.set(name, channels);
      pending.delete(name);
    }
  }
} catch (e) { error = e.message; }
const checkpoints = [...outputs].filter(([name]) => name.startsWith('Checkpoint ')).map(([name,data])=>({name,value:data[0][0]?.json}));
process.stdout.write(JSON.stringify({error, result:outputs.get('Result')?.[0]?.[0]?.json || null, executed, checkpoints}));
