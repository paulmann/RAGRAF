/**
 * RAGRAF for DeepSeek Harness — a patch-only bundle.
 *
 * The bundle contributes a patch layer (`cordis.patch.yml`) and declares the
 * hook bridge as its dependency; it has no runtime plugin of its own, so this
 * module is empty on purpose. The manager still validates that the declared
 * entry point exists, which is why the file is here at all.
 *
 * Everything that runs at hook time lives in `scripts/dispatch.py`, and every
 * path it needs is read from the machine through RAGRAF itself: the package is
 * the same on every computer, the location of the installation is not.
 */

export {}
