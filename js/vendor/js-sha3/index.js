// ESM facade over the unmodified UMD build: in ES-module scope neither
// `module` nor `define` exists, so sha3.js falls through to attaching
// its methods to the global object (window / self / global); re-export
// the one method swarmlite uses.
import './sha3.js';

/** keccak256 over a string or byte array -> lowercase hex (no 0x). */
export const keccak256 = globalThis.keccak_256;
