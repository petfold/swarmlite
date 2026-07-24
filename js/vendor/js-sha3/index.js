// ESM facade over the unmodified UMD build: in ES-module scope neither
// `module` nor `define` exists, so sha3.js falls through to attaching
// its methods to the global object (window / self / global); re-export
// the one method swarmlite uses.
import './sha3.js';

/** keccak256 over a string or byte array -> lowercase hex (no 0x). */
export const keccak256 = globalThis.keccak_256;

/** keccak256 over a byte array -> Uint8Array (for BMT/SOC hashing). */
export const keccak256Bytes = (data) =>
  Uint8Array.from(globalThis.keccak_256.array(data));
