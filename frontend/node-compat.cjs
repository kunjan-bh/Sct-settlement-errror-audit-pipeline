const util = require('node:util');
if (!util.styleText) {
  util.styleText = (_styles, text) => text;
}
if (!util.parseEnv) {
  util.parseEnv = () => ({});
}
if (!util.stripVTControlCharacters) {
  util.stripVTControlCharacters = (s) => s;
}
if (!globalThis.CustomEvent) {
  globalThis.CustomEvent = class CustomEvent extends Event {
    constructor(event, params = {}) {
      super(event, params);
      this.detail = params.detail;
    }
  };
}
