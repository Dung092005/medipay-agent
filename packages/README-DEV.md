# Packages — developer guide

Packages are optional offline compatibility boundaries. The online API image
must import from `src/` and must not install corpus tooling. Build/test a
package explicitly from its own `pyproject.toml` before publishing it.
