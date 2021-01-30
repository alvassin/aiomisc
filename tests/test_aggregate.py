import asyncio
import math
from asyncio import Event, wait
from time import monotonic
from typing import List

from aiocontextvars import ContextVar
import pytest

from aiomisc.aggregate import aggregate


async def test_invalid_func():
    with pytest.raises(ValueError) as excinfo:
        @aggregate(10)
        async def pho(a, b=1):
            pass
    assert str(excinfo.value) == (
        "Function must accept variadic positional arguments"
    )


@pytest.mark.parametrize('leeway_ms', (-1.0, 0.0))
async def test_invalid_leeway(leeway_ms):
    with pytest.raises(ValueError) as excinfo:
        @aggregate(leeway_ms)
        async def pho(*args):
            pass

    assert str(excinfo.value) == "leeway_ms must be positive float"


@pytest.mark.parametrize('max_count', (-1, 0))
async def test_invalid_max_count(max_count):
    with pytest.raises(ValueError) as excinfo:
        @aggregate(10, max_count)
        async def pho(*args):
            pass

    assert str(excinfo.value) == "max_count must be positive int or None"


async def test_error(loop):
    t_exec = 0
    event = Event()
    leeway = 0.01

    @aggregate(leeway * 1000)
    async def pow(*args: float, power: float = 2) -> List[float]:
        nonlocal t_exec
        t_exec = monotonic()
        event.set()

        raise ValueError

    async def pho(num: int):
        return await pow(float(num))

    tasks = []
    for i in range(10):
        tasks.append(loop.create_task(pho(i)))

    t = monotonic()
    await event.wait()
    elapsed = t_exec - t
    assert leeway < elapsed < leeway * 2

    await wait(tasks)
    for task in tasks:
        assert task.done()
        assert isinstance(task.exception(), ValueError)


async def test_leeway_ok(loop):
    t_exec = 0
    event = Event()
    leeway = 0.1

    @aggregate(leeway * 1000)
    async def pow(*args: float, power: float = 2) -> List[float]:
        nonlocal t_exec
        t_exec = monotonic()
        event.set()

        return [math.pow(num, power) for num in args]

    tasks = []
    for i in range(9):
        tasks.append(loop.create_task(pow(i)))

    t = monotonic()
    await asyncio.sleep(leeway * 0.1)
    assert all(not task.done() for task in tasks)

    await event.wait()
    elapsed = t_exec - t
    assert leeway < elapsed < leeway * 2

    await wait(tasks)
    for i, task in enumerate(tasks):
        assert task.done()
        assert task.result() == math.pow(i, 2)


async def test_max_count(loop):
    t_exec = 0
    event = Event()
    leeway = 0.1
    max_count = 5

    @aggregate(leeway * 1000, max_count)
    async def pow(*args: float, power: float = 2) -> List[float]:
        nonlocal t_exec
        t_exec = monotonic()
        event.set()

        return [math.pow(num, power) for num in args]

    tasks = []
    for i in range(5):
        tasks.append(loop.create_task(pow(i)))

    t = monotonic()
    await event.wait()
    elapsed = t_exec - t
    assert 0 < elapsed < leeway

    await wait(tasks)
    for i, task in enumerate(tasks):
        assert tasks[i].done()
        assert task.result() == math.pow(i, 2)


async def test_max_count_multiple_batches(loop):
    t_exec = 0
    event = Event()
    leeway = 0.1
    max_count = 5

    @aggregate(leeway * 1000, max_count)
    async def pow(*args: float, power: float = 2) -> List[float]:
        nonlocal t_exec
        t_exec = monotonic()
        event.set()

        return [math.pow(num, power) for num in args]

    tasks = []
    for i in range(9):
        tasks.append(loop.create_task(pow(i)))

    # Wait for the first batch
    t = monotonic()
    await event.wait()
    event.clear()
    elapsed = t_exec - t
    assert 0 < elapsed < leeway

    await wait(tasks[:5])
    for i in range(5):
        assert tasks[i].done()
    for i in range(5, 9):
        assert not tasks[i].done()

    # Wait for the second batch
    await event.wait()
    elapsed = t_exec - t
    assert leeway < elapsed < leeway * 2

    await wait(tasks[5:])
    for i, task in enumerate(tasks):
        assert task.done()
        assert task.result() == math.pow(i, 2)


async def test_leeway_cancel(loop):
    t_exec = 0
    delay_exec = 0.1
    event = Event()
    executions = 0
    leeway = 0.1
    arg = ContextVar('arg')
    tasks = []
    executing_task = None

    @aggregate(leeway * 1000)
    async def pow(*args: float, power: float = 2) -> List[float]:
        nonlocal executions, executing_task, t_exec, delay_exec
        t_exec = monotonic()
        executions += 1
        executing_task = tasks[arg.get()]
        event.set()

        await asyncio.sleep(delay_exec)
        return [math.pow(num, power) for num in args]

    async def pho(num: int):
        arg.set(num)
        return await pow(float(num))

    for i in range(9):
        tasks.append(loop.create_task(pho(i)))

    # Execution must have started
    t = monotonic()
    await event.wait()
    event.clear()
    elapsed = t_exec - t
    assert leeway < elapsed < leeway * 2
    assert executions == 1
    first_executing_task = executing_task
    first_executing_task.cancel()

    # Another task must have tried to execute
    await event.wait()
    assert executions == 2
    assert first_executing_task.cancelled()
    assert all(
        not task.done() for task in tasks
        if task is not first_executing_task
    )

    # Must have finished
    await wait(tasks)
    assert first_executing_task.cancelled()
    for i, task in enumerate(tasks):
        if task is first_executing_task:
            continue
        assert task.done()
        assert task.result() == math.pow(i, 2)


async def test_max_count_cancel(loop):
    t_exec = 0
    delay_exec = 0.1
    event = Event()
    executions = 0
    leeway = 100
    max_count = 5
    arg = ContextVar('arg')
    tasks = []
    executing_task = None

    @aggregate(leeway * 1000, max_count)
    async def pow(*args: float, power: float = 2) -> List[float]:
        nonlocal executions, executing_task, t_exec, delay_exec
        t_exec = monotonic()
        executions += 1
        executing_task = tasks[arg.get()]
        event.set()

        await asyncio.sleep(delay_exec)
        return [math.pow(num, power) for num in args]

    async def pho(num: int):
        arg.set(num)
        return await pow(float(num))

    tasks = []
    for i in range(5):
        tasks.append(loop.create_task(pho(i)))

    # Execution must have started
    t = monotonic()
    await event.wait()
    event.clear()
    elapsed = t_exec - t
    assert 0 < elapsed < leeway
    assert all(not task.done() for task in tasks)
    assert executions == 1
    first_executing_task = executing_task
    first_executing_task.cancel()

    # Another task must have tried to execute
    await event.wait()
    assert executions == 2
    assert first_executing_task.cancelled()
    assert all(
        not task.done() for task in tasks
        if task is not first_executing_task
    )

    # Must have finished
    await wait(tasks)
    assert first_executing_task.cancelled()
    for i, task in enumerate(tasks):
        if task is first_executing_task:
            continue
        assert task.done()
        assert task.result() == math.pow(i, 2)


async def test_max_count_multiple_batches_cancel(loop):
    t_exec = 0
    delay_exec = 0.1
    event = Event()
    executions = 0
    leeway = 0.1
    max_count = 5
    arg = ContextVar('arg')
    tasks = []
    executing_task = None

    @aggregate(leeway * 1000, max_count)
    async def pow(*args: float, power: float = 2) -> List[float]:
        nonlocal executions, executing_task, t_exec, delay_exec
        t_exec = monotonic()
        executions += 1
        executing_task = tasks[arg.get()]
        event.set()

        await asyncio.sleep(delay_exec)
        return [math.pow(num, power) for num in args]

    async def pho(num: int):
        arg.set(num)
        return await pow(float(num))

    tasks = []
    for i in range(9):
        tasks.append(loop.create_task(pho(i)))

    t = monotonic()

    # Execution of the first batch must have started
    await event.wait()
    event.clear()
    assert all(not task.done() for task in tasks)
    assert executions == 1
    first_executing_task = executing_task
    first_executing_task.cancel()

    # Another task must have tried to execute
    await event.wait()
    event.clear()
    assert executions == 2
    assert first_executing_task.cancelled()
    assert all(
        not task.done() for task in tasks
        if task is not first_executing_task
    )

    await wait(tasks[:5])
    # First batch must have finished
    assert first_executing_task.cancelled()
    for i, task in enumerate(tasks[:5]):
        if task is first_executing_task:
            continue
        assert task.done()
        assert task.result() == math.pow(i, 2)

    # Second batch must have started execution
    await event.wait()
    elapsed = t_exec - t
    assert leeway < elapsed < leeway * 2
    assert all(not task.done() for task in tasks[5:])
    assert executions == 3

    # Second batch mast have finished
    await wait(tasks[5:])
    for i, task in enumerate(tasks[5:], start=5):
        assert task.done()
        assert task.result() == math.pow(i, 2)
