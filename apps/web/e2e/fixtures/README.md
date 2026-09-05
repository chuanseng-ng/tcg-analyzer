# Fixture photographs

Synthetic, rendered by the API journey's generators in
`services/api/tests/test_anonymous_journey.py` — `front.jpg` is
`photograph(seed=1)`, `back.jpg` is `photograph(seed=2)`, both through `jpeg()`
(quality 95), and `unusable.jpg` is `unusable_photograph()`, a 64×48 JPEG below
the gate's 640 px short edge. They are photographs of nothing and carry no
provenance question. To regenerate, from the repository root:

```bash
uv run python -c "
import sys; sys.path.insert(0, 'services/api/tests')
from pathlib import Path
from test_anonymous_journey import photograph, jpeg, unusable_photograph
out = Path('apps/web/e2e/fixtures')
(out / 'front.jpg').write_bytes(jpeg(photograph(seed=1)))
(out / 'back.jpg').write_bytes(jpeg(photograph(seed=2)))
(out / 'unusable.jpg').write_bytes(unusable_photograph())
"
```
