function need(condition, message) { if (!condition) throw new Error(message); }
const operations = {
  'fixture.decode': ({text}) => ({value: JSON.parse(text)}),
};
