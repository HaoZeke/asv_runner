# Developing benchmarks

All benchmark plugins must follow a strict hierarchy:

- The package name must begin with `asv_bench`. The distribution name may use
  dashes, dots or capitals (`asv-bench-foo`); it is normalised to the package
  name (`asv_bench_foo`).
- Benchmark classes are defined in a `benchmarks` folder under the package module.
- Each exported new benchmark type has the `export_as_benchmark = [NAMEBenchmark]` attribute.

For more conventions, see the internally defined benchmark types within `asv_runner`.
