# Pinned OvisOCR3 reference implementation

Source: https://huggingface.co/ATH-MaaS/OvisOCR3/tree/5b252386ba615bd464a5a26597560eaba87dae5c

`document_crosspage.py` and `assets/mathjax.js` are copied byte-for-byte from that revision (2026-11-10), with the model repository's Apache-2.0 license. MathJax is an Apache-2.0 third-party component; see https://github.com/mathjax/MathJax-src/blob/3.2.2/LICENSE.

The service supplies its HTTP backend explicitly; it never instantiates VllmBackend. Browser dependencies load only for the optional document-level tool. Keep this code unchanged; put service adaptation and resource limits in `recursive_tables.py`.
