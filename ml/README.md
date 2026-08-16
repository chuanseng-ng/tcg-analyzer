# `ml/`

Machine-learning modules. Python, members of the `uv` workspace.

The pipeline shape is mandatory (spec §2.2):

```text
images → one neutral condition representation → {PSA model, TAG model, BGS model}
```

There is never a single universal `condition_score → grade` mapping. Every model
is versioned and immutable; nothing references `/latest/`.

**Never commit model weights or training images.**
