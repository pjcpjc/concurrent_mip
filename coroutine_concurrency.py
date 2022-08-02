# Using a .py file here instead of a notebook because jupyter itself runs an event loop. Rare situation
# where jupyter is a poor demonstration tool. At any rate, this achieves no meaningful performance
# advantage, which shouldn't be too surprising but I wanted to check it out anyway.

import time
import cogmodel
import asyncio
from ticdat import Progress, LogFile

dat = cogmodel.input_schema.json.create_tic_dat("cog_sample_data.json")
dat.parameters["Gurobi LogToConsole"] = 0
assert dat.parameters["Number of Centroids"]["Value"] == 3

dat_2 = cogmodel.input_schema.copy_tic_dat(dat)
dat_2.parameters["Number of Centroids"] = 4
dat_3 = cogmodel.input_schema.copy_tic_dat(dat)
dat_3.parameters["Number of Centroids"] = 5

# there is likely a cleaner way to handle capturing the solution -  feel free to suggest one
sln_bfr = {}
async def solve_to_bfr(dat_):
    sln = cogmodel.solve(dat_, LogFile(None), LogFile(None), Progress())
    if sln:
        sln_bfr[len(sln.openings)] = sln

async def solve(dat_):
    await solve_to_bfr(dat_)

async def main():
    await asyncio.gather(solve(dat), solve(dat_2), solve(dat_3))

start = time.time()
asyncio.run(main()) # use this outside of Jupyter
assert set(sln_bfr) == {3, 4, 5}

print(f"\n\n****\nRequired {time.time() - start} seconds in total")