// This catalog delegates tool operations to the world and model operations to Agency.
function need(condition, message) { if (!condition) throw new Error(message); }
const operations = {};
