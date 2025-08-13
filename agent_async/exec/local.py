import asyncio
from typing import AsyncIterator, Optional


class LocalExecutor:
    def __init__(self, cwd):
        self.cwd = str(cwd)

    async def run(self, cmd: str) -> AsyncIterator[tuple[str, str]]:
        """
        Run a shell command, yielding tuples of (stream, text) where stream is
        "stdout" or "stderr" and text is the chunk.
        """
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=self.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def read_stream(stream, name):
            while True:
                data = await stream.read(1024)
                if not data:
                    break
                yield name, data.decode(errors="replace")

        # Concurrently read both streams
        stdout_iter = read_stream(proc.stdout, "stdout")
        stderr_iter = read_stream(proc.stderr, "stderr")

        pending = {asyncio.create_task(stdout_iter.__anext__()), asyncio.create_task(stderr_iter.__anext__())}
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    item = task.result()
                except StopAsyncIteration:
                    # stop creating new tasks for this iterator
                    continue
                else:
                    stream_name, text = item
                    yield stream_name, text
                    # re-queue task for next chunk
                    if stream_name == "stdout":
                        pending.add(asyncio.create_task(stdout_iter.__anext__()))
                    else:
                        pending.add(asyncio.create_task(stderr_iter.__anext__()))

        await proc.wait()

