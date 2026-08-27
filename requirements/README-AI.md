# Python dependency sets — AI contract

`runtime.lock` is the only dependency input for the online API image.
`migrate.lock` and `pipeline.lock` are isolated least-privilege jobs;
`dev.lock` is not deployable. Keep the sets minimal, hash-pinned and free of
secrets or local paths. When changing a dependency, update the corresponding
source `.in`, regenerate the lock and verify the Docker/build contract.
