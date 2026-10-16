"use strict";

// Browser File objects can become unreadable when their original filesystem
// handle changes. Material inspection and OCR submission therefore share one
// in-memory byte snapshot.
(function installOcrFileCache(global) {
  function bytesToBase64(bytes) {
    const pieces = [];
    const chunkSize = 0x8000;
    for (let offset = 0; offset < bytes.length; offset += chunkSize) {
      const chunk = bytes.subarray(offset, Math.min(offset + chunkSize, bytes.length));
      pieces.push(String.fromCharCode(...chunk));
    }
    return btoa(pieces.join(""));
  }

  async function cacheMaterialFile(file) {
    if (file.cachedBytes instanceof ArrayBuffer && file.cachedBytes.byteLength) {
      return new Uint8Array(file.cachedBytes);
    }
    if (!file.raw) {
      throw new Error(`“${file.name}”没有可读取的本机文件，请删除后重新添加。`);
    }
    try {
      const bytes = new Uint8Array(await file.raw.arrayBuffer());
      const snapshot = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
      file.cachedBytes = snapshot;
      return new Uint8Array(snapshot);
    } catch (error) {
      throw new Error(
        `无法读取“${file.name}”的本机源文件。请删除此文件后重新添加，再启动 OCR。`
      );
    }
  }

  async function fileAsBase64(file) {
    return bytesToBase64(await cacheMaterialFile(file));
  }

  global.OAOcrFileCache = { cacheMaterialFile, fileAsBase64 };
})(globalThis);
