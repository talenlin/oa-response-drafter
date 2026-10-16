import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../web/ocr-file-cache.js", import.meta.url), "utf8");
const context = {
  ArrayBuffer,
  Uint8Array,
  btoa: (binary) => Buffer.from(binary, "binary").toString("base64"),
};
context.globalThis = context;
vm.runInNewContext(source, context, { filename: "ocr-file-cache.js" });

let reads = 0;
const material = {
  name: "需要 OCR 的材料.pdf",
  raw: {
    async arrayBuffer() {
      reads += 1;
      if (reads > 1) throw new Error("The requested file could not be read");
      return Uint8Array.from([1, 2, 3]).buffer;
    },
  },
};

await context.OAOcrFileCache.cacheMaterialFile(material);
const encoded = await context.OAOcrFileCache.fileAsBase64(material);

assert.equal(encoded, "AQID");
assert.equal(reads, 1, "OCR submission must reuse the initial local file read");
console.log("OCR file snapshot regression test passed");
