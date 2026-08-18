# Third-party notices

CLIO Web Search 0.3.0 combines independently licensed open-source components. The
container and Python lockfile retain the complete dependency graph; this file
highlights the major runtime components and immutable pins.

| Component | Pin | License / source |
|---|---|---|
| SearXNG | commit `e8e710e42a3ab2bce27d1f97e51d8d4ccaa80871` | AGPL-3.0-or-later, <https://github.com/searxng/searxng> |
| GROBID | `grobid/grobid:0.9.0-crf`, manifest digest `sha256:24ba90eb1c959f65d812bcdb2cf79c677fa5fd7b95235de616b8bc9fa1317849` | Apache-2.0, <https://github.com/kermitt2/grobid> |
| Docling | `2.119.0` | MIT, <https://github.com/docling-project/docling> |
| Valkey | `8.1.9`, source SHA-256 `f7e927534aaeb3a5f4410375c3e6f2f0c1b9257db8bc573e68f9e2dbb11344f4` | BSD-3-Clause, <https://github.com/valkey-io/valkey> |
| uv | `0.8.12` | Apache-2.0 OR MIT, <https://github.com/astral-sh/uv> |

Docling downloads its layout, TableFormerV2, and RapidOCR artifacts during the
image build. Those model artifacts remain subject to their upstream model-card
and license terms. Python package license metadata remains installed in the
image virtual environments.

The repository is distributed under AGPL-3.0-or-later; the complete license
text is in [LICENSE](LICENSE). This notice does not replace any component's own
license terms.
