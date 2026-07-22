const util = require('node:util');
if (!util.styleText) {
  util.styleText = (_styles, text) => text;
}
