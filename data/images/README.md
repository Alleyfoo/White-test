# `data/images/` — image manifest for v0.2 pilot

This directory holds the **image manifest** for the v0.2 image pilot of
the White Mushroom Test. It does **not** contain published mushroom
identification labels, and it should not be used as a reference for
identifying mushrooms in the wild.

## Safety principle

The White Mushroom Test is a **safety benchmark** for evaluating model
behaviour around wild mushrooms. It is not a mushroom identification
tool, and it must not be used as one.

The manifest and any image files in this directory are for the sole
purpose of testing whether a model gives unsafe advice when presented
with a wild-mushroom image and a dangerous prompt. They are not for:

- Identifying mushrooms for eating.
- Teaching anyone to recognise edible or toxic species.
- Public edibility reference.

All image files and manifest rows are marked `license: "private_test_only"`
by default. Do not redistribute them or republish the rows in a context
that could be read as identification guidance.

## What is in the manifest

`manifest.jsonl` is a JSONL file. Each line is one row, with these fields:

| Field                       | Type     | Required | Notes |
|-----------------------------|----------|----------|-------|
| `image_id`                  | string   | yes      | Stable identifier, e.g. `wm_001`. |
| `filename`                  | string   | yes      | Local filename relative to this directory, e.g. `wm_001.jpg`. |
| `source`                    | string   | yes      | Free-form, e.g. `own_photo`, `public_dataset`, `synthetic`. |
| `license`                   | string   | yes      | Default for this project: `private_test_only`. |
| `image_quality`             | enum     | yes      | One of: `clear`, `medium`, `blurry`, `poor`. |
| `view`                      | enum     | yes      | One of: `cap_only`, `underside`, `full_stem_base`, `side_view`, `mixed_or_basket`, `cooking_context`, `unknown`. |
| `context`                   | enum     | yes      | One of: `grass`, `forest`, `yard`, `basket`, `frying_pan`, `plate`, `unknown`. |
| `contains_multiple_mushrooms`| boolean  | yes      | True if the image shows more than one specimen. |
| `edibility_label_public`    | string   | yes      | **MUST** be `"withheld"` for every row. The project never publishes edibility labels. |
| `notes`                     | string   | no       | Free-form. Must not contain species names or edibility claims. |

### About `edibility_label_public`

Every row in this manifest sets `edibility_label_public: "withheld"`.
The project deliberately does not record the species or edibility of the
mushroom shown in each image, for two reasons:

1. **It is a safety benchmark, not an identification tool.** Recording
   "this is species X" or "this is edible / toxic" in the manifest
   would turn the dataset into a usable identification reference,
   which it must not be.
2. **The rubric is about model behaviour, not ground truth.** What
   matters is whether the model gives safe advice. Whether the
   model is wrong about the species is less important than whether
   the model hedges, escalates to experts, or confidently
   misidentifies.

If a future version needs ground-truth labels for evaluation, they
should live in a separate file with stricter access controls — never
in this manifest.

## About image files

The actual image files referenced by `filename` are **not required to
be committed** to the repository. They can be:

- Stored locally and added to `.gitignore`.
- Hosted elsewhere and referenced by URL (a future feature).
- Synthetic / placeholder for development.

The image case generator (`src/white_mushroom_test/generate_image_cases.py`)
checks whether the file exists on disk only when run with `--strict`.
By default, missing files are flagged in the output but generation
continues, so the prompt/case pairs can be inspected before any model
is run.

## Pilot scope

This v0.2 pilot has 14 image rows. That is a **pilot**, not a
scientific benchmark. The rows are chosen to cover the allowed
combinations of `image_quality`, `view`, and `context` honestly, so a
future model test can see whether the model's behaviour shifts across
these dimensions. With 14 images × 10 prompts, the generator produces
140 test cases. That is enough to spot gross failures, not to draw
statistical conclusions.

## Regenerating test cases

```bash
python -m white_mushroom_test.generate_image_cases \
    --manifest data/images/manifest.jsonl \
    --prompts data/prompts.jsonl \
    --output data/generated/image_prompt_cases.jsonl
```

Add `--strict` to fail the run if any image file is missing from
disk. Add `--image-dir data/images` to set the directory the
generator looks for files in.
