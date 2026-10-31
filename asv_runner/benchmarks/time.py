import gc
import re
import sys
import timeit

from ._base import Benchmark, _get_first_attr

wall_timer = timeit.default_timer


class _SetupInterleavedTimer:
    """
    ``timeit.Timer``-compatible batching for explicit ``number > 1`` with
    setup hooks (asv#966): setup re-runs before every call via
    ``redo_setup`` and only the calls accumulate into the sample, at the
    cost of two clock reads per call. Garbage collection is disabled for
    the duration of a sample, matching ``timeit.Timer.timeit``.
    """

    def __init__(self, func, redo_setup, timer):
        self._func = func
        self._redo_setup = redo_setup
        self._timer = timer

    def timeit(self, number):
        func = self._func
        redo_setup = self._redo_setup
        timer = self._timer
        total = 0.0
        gcold = gc.isenabled()
        gc.disable()
        try:
            for _ in range(number):
                redo_setup()
                start = timer()
                func()
                total += timer() - start
        finally:
            if gcold:
                gc.enable()
        return total


class TimeBenchmark(Benchmark):
    """
    Represents a single benchmark for timing.

    This class inherits from `Benchmark` and is specialized for timing benchmarks.

    #### Attributes
    **name_regex** (`re.Pattern`)
    : Regular expression that matches the name of the timing benchmarks.

    **rounds** (`int`)
    : Number of rounds to execute the benchmark.

    **repeat** (`int`)
    : Number of times the code will be repeated during each round.

    **min_run_count** (`int`)
    : Minimum number of runs required for the benchmark.

    **number** (`int`)
    : The argument to `timeit.timeit`, specifying the number of executions of
    the setup statement.

    **sample_time** (`float`)
    : The target time for each sample.

    **warmup_time** (`float`)
    : The time spent warming up the benchmark.

    **timer** (`callable`)
    : The timer to use, by default it uses `timeit.default_timer`.

    **setup_scope** (`str`)
    : ``"call"`` (default) or ``"sample"``. ``"call"`` re-runs setup
    before every timed call when a setup hook exists. ``"sample"`` runs
    setup once per sample and batches the timed calls.
    """

    name_regex = re.compile("^(Time[A-Z_].+)|(time_.+)$")

    # With setup hooks present, every timed call observes freshly
    # set-up state (asv#966): auto-calibrated number resolves to 1, and
    # an explicit number > 1 batches through _SetupInterleavedTimer.
    # Subclasses whose samples do not share in-process state (timeraw:
    # one subprocess per sample) opt out.
    _setup_isolates_timed_calls = True

    def __init__(self, name, func, attr_sources):
        """
        Initialize a new instance of `TimeBenchmark`.

        #### Parameters
        **name** (`str`)
        : The name of the benchmark.

        **func** (`callable`)
        : The function to benchmark.

        **attr_sources** (`list`)
        : A list of objects from which to draw attributes.
        """
        Benchmark.__init__(self, name, func, attr_sources)
        self.type = "time"
        self.unit = "seconds"
        self._attr_sources = attr_sources
        old = int(
            _get_first_attr(self._attr_sources, "processes", 2)
        )  # backward compat.
        self.rounds = int(_get_first_attr(self._attr_sources, "rounds", old))
        self._load_vars()

    def _load_vars(self):
        """Loads benchmark variables from attribute sources."""
        self.repeat = _get_first_attr(self._attr_sources, "repeat", 0)
        self.min_run_count = _get_first_attr(self._attr_sources, "min_run_count", 2)
        self.number = int(_get_first_attr(self._attr_sources, "number", 0))
        self.sample_time = _get_first_attr(self._attr_sources, "sample_time", 0.01)
        self.warmup_time = _get_first_attr(self._attr_sources, "warmup_time", -1)
        self.timer = _get_first_attr(self._attr_sources, "timer", wall_timer)
        # "call": setup before every timed call (default, asv#966).
        # "sample": setup once per sample, then the statement `number` times.
        self.setup_scope = _get_first_attr(self._attr_sources, "setup_scope", "call")

    def do_setup(self):
        """Execute the setup method and load variables."""
        result = Benchmark.do_setup(self)
        # For parameterized tests, setup() is allowed to change these
        self._load_vars()
        return result

    def _param_bound_func(self, *param):
        if param:

            def func():
                self.func(*param)

            return func
        return self.func

    def _get_timer(self, *param):
        """Get a `timeit.Timer` for the current benchmark."""
        func = self._param_bound_func(*param)
        timer = timeit.Timer(stmt=func, setup=self.redo_setup, timer=self.timer)
        return timer

    def _get_interleaved_timer(self, *param):
        """Get a per-call-timed batch timer that re-runs setup between calls."""
        func = self._param_bound_func(*param)
        return _SetupInterleavedTimer(func, self.redo_setup, self.timer)

    def run(self, *param):
        """
        Run the benchmark with the given parameters.

        #### Parameters
        **param** (`tuple`)
        : The parameters to pass to the benchmark function.

        #### Returns
        **result** (`dict`)
        : A dictionary with the benchmark results. It contains the samples taken
        and the number of times the function was called in each sample.

        #### Notes
        The benchmark timing method is designed to adaptively find an optimal
        `number` of function executions to time based on the estimated
        performance. This number is then used for the final timings.

        The warmup time is determined based on the Python interpreter in use.
        PyPy and GraalPython need longer warmup times due to their JIT
        compilers. For CPython, a short warmup time is used to account for
        transient effects such as OS scheduling.

        The `repeat` attribute specifies how many times to run the function for
        timing. It can be an integer, meaning the function is run that many
        times, or a tuple of three values, specifying the minimum number of
        runs, the maximum number of runs, and the maximum total time to spend on
        runs.

        After obtaining the timing samples, each sample is divided by the
        `number` of function executions to get the average time per function
        call, and these values are returned as the "samples" in the result.
        """
        warmup_time = self.warmup_time
        if warmup_time < 0:
            if "__pypy__" in sys.modules:
                warmup_time = 1.0
            elif "__graalpython__" in sys.modules:
                warmup_time = 5.0
            else:
                # Transient effects exist also on CPython, e.g. from
                # OS scheduling
                warmup_time = 0.1

        try:
            min_repeat, max_repeat, max_time = self.repeat
        except (ValueError, TypeError):
            if self.repeat == 0:
                min_repeat = 1
                max_repeat = 10
                max_time = 20.0
                if self.rounds > 1:
                    max_repeat //= 2
                    max_time /= 2.0
            else:
                min_repeat = self.repeat
                max_repeat = self.repeat
                max_time = self.timeout
                # XXX: This is a bug, needed for --quick
                # gh-1308 in asv
                if max_time is None:
                    max_time = 60.0

        min_repeat = int(min_repeat)
        max_repeat = int(max_repeat)
        max_time = float(max_time)

        number = self.number
        # timeit(number=N) runs setup once, then the stmt N times: setup
        # (via redo_setup) is per sample, never between the inner calls.
        # setup_scope "call" (default): every timed call observes freshly
        # set-up state (asv#966). Auto number resolves to 1 under plain
        # timeit, and an explicit number > 1 batches through
        # _SetupInterleavedTimer (setup between calls, calls timed
        # individually).
        # setup_scope "sample": plain timeit batching, including when a
        # setup hook exists. Auto number is calibrated to sample_time.
        # Without setup hooks, plain timeit batching is unchanged.
        scope = self.setup_scope
        if scope not in ("call", "sample"):
            raise ValueError(
                f"asv: {self.name}: setup_scope must be 'call' or 'sample', "
                f"got {scope!r}"
            )
        isolate = (
            self._setups and self._setup_isolates_timed_calls and scope == "call"
        )
        if isolate and number == 0:
            number = 1
        if isolate and number > 1:
            print(
                f"asv: {self.name}: setup hooks re-run between the "
                f"{number} individually timed calls of each sample",
                file=sys.stderr,
            )
            timer = self._get_interleaved_timer(*param)
        else:
            timer = self._get_timer(*param)

        samples, number = self.benchmark_timing(
            timer,
            min_repeat,
            max_repeat,
            max_time=max_time,
            warmup_time=warmup_time,
            number=number,
            min_run_count=self.min_run_count,
        )

        samples = [s / number for s in samples]
        return {"samples": samples, "number": number}

    def benchmark_timing(
        self,
        timer,
        min_repeat,
        max_repeat,
        max_time,
        warmup_time,
        number,
        min_run_count,
    ):
        """
        Benchmark the timing of the function execution.

        #### Parameters
        **timer** (`timeit.Timer`)
        : The timer to use for the benchmarking.

        **min_repeat** (`int`)
        : The minimum number of times to repeat the function execution.

        **max_repeat** (`int`)
        : The maximum number of times to repeat the function execution.

        **max_time** (`float`)
        : The maximum total time to spend on the benchmarking.

        **warmup_time** (`float`)
        : The time spent warming up the benchmark.

        **number** (`int`)
        : The number of executions of the timed statement per sample
        (``timeit``'s ``number``). Without setup hooks, setup runs once
        per sample; with setup hooks the timer re-runs setup before
        every execution and only the executions are timed.

        **min_run_count** (`int`)
        : The minimum number of runs required for the benchmark.

        #### Returns
        **result** (`tuple`)
        : A tuple with the samples taken and the number of times the function
        was called in each sample.

        #### Notes
        The `too_slow` internal function is used to stop taking samples when
        certain limits are exceeded. These limits are the minimum run count, the
        minimum repeat count, and the maximum time.

        If `number` is zero, a suitable number of function executions is
        estimated, and the system is warmed up at the same time.

        If the warmup time is greater than zero, a warmup phase is initiated
        where the function is called repeatedly until the warmup time has
        passed.

        After these initial steps, the function execution times are sampled and
        added to the `samples` list, stopping when reaching the maximum repeat
        count or when the `too_slow` function indicates to stop.
        """
        sample_time = self.sample_time
        start_time = wall_timer()
        run_count = 0

        samples = []

        def too_slow(num_samples):
            # stop taking samples if limits exceeded
            if run_count < min_run_count:
                return False
            if num_samples < min_repeat:
                return False
            return wall_timer() > start_time + warmup_time + max_time

        if number == 0:
            # Select number & warmup.
            #
            # This needs to be done at the same time, because the
            # benchmark timings at the beginning can be larger, and
            # lead to too small number being selected.
            number = 1
            while True:
                self._redo_setup_next = False
                start = wall_timer()
                timing = timer.timeit(number)
                wall_time = wall_timer() - start
                actual_timing = max(wall_time, timing)
                run_count += number

                if actual_timing >= sample_time:
                    if wall_timer() > start_time + warmup_time:
                        break
                else:
                    try:
                        p = min(10.0, max(1.1, sample_time / actual_timing))
                    except ZeroDivisionError:
                        p = 10.0
                    number = max(number + 1, int(p * number))

            if too_slow(1):
                return [timing], number
        elif warmup_time > 0:
            # Warmup
            while True:
                if not self._setups:
                    # Skipping redo_setup keeps warmup cheap; with setup
                    # hooks present, warmup must observe the same
                    # freshly-set-up state as the measurement loop below
                    # (asv#966).
                    self._redo_setup_next = False
                timing = timer.timeit(number)
                run_count += number
                if wall_timer() >= start_time + warmup_time:
                    break

            if too_slow(1):
                return [timing], number

        # Collect samples
        while len(samples) < max_repeat:
            timing = timer.timeit(number)
            run_count += number
            samples.append(timing)

            if too_slow(len(samples)):
                break

        return samples, number


export_as_benchmark = [TimeBenchmark]
