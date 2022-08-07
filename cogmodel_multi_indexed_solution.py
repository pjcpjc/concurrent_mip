#
# Similar to cogmodel, except demonstrates how multiple solves can be run in parallel quite easily
# due to the magic of Gurobi's solve routine escaping the GIL
#
#

from threading import Thread
import time
import gurobipy as gu
from ticdat import TicDatFactory, Progress, LogFile, Slicer, standard_main, gurobi_env

# ------------------------ define the input schema --------------------------------
# There are three input tables, with 4 primary key fields  and 4 data fields.
input_schema = TicDatFactory (
     solutions = [["Name"], ["Number of Centroids"]],
     sites      = [['Name'],['Demand', 'Center Status']],
     distance   = [['Source', 'Destination'],['Distance']],
     parameters = [["Parameter"], ["Value"]])

input_schema.set_data_type("solutions", "Number of Centroids", must_be_int=True, min=1, max=float("inf"))
input_schema.set_default_value("solutions", "Number of Centroids", 1)
# add foreign key constraints
input_schema.add_foreign_key("distance", "sites", ['Source', 'Name'])
input_schema.add_foreign_key("distance", "sites", ['Destination', 'Name'])

# center_status is a flag field which can take one of two string values.
input_schema.set_data_type("sites", "Center Status", number_allowed=False,
                          strings_allowed=["Can Be Center", "Pure Demand Point"])
input_schema.set_default_value("sites", "Center Status", "Pure Demand Point")

# The default type of non infinite, non negative works for distance and demand
input_schema.set_data_type("sites", "Demand")
input_schema.set_data_type("distance", "Distance")

input_schema.add_parameter("Threading", default_value="Enabled", number_allowed=False,
                           strings_allowed=["Enabled", "Disabled"])
input_schema.add_parameter("Gurobi LogToConsole", default_value=1, inclusive_min=True, inclusive_max=True, min=0,
                            max=1, must_be_int=True)
input_schema.add_parameter("MIP Gap", default_value=0.001, inclusive_min=False, inclusive_max=False, min=0,
                                max=float("inf"), must_be_int=False)
input_schema.add_parameter("Formulation", "Strong", number_allowed=False, strings_allowed=["Weak", "Strong"])
# ---------------------------------------------------------------------------------


# ------------------------ define the output schema -------------------------------
# There are three solution tables, with 2 primary key fields and 3
# data fields amongst them.
solution_schema = TicDatFactory(
    openings    = [['Solution', 'Site'],[]],
    assignments = [['Solution', 'Site', 'Assigned To'],[]],
    parameters  = [['Solution', "Parameter"], ["Value"]])
# ---------------------------------------------------------------------------------

# ------------------------ create a solve function --------------------------------

def _make_model_dat(dat, number_centroids):

    def get_distance(x, y):
        if (x, y) in dat.distance:
            return dat.distance[x, y]["Distance"]
        if (y, x) in dat.distance:
            return dat.distance[y, x]["Distance"]
        return float("inf")

    def can_assign(x, y):
        return dat.sites[y]["Center Status"] == "Can Be Center" \
               and get_distance(x,y)<float("inf")


    unassignables = [n for n in dat.sites if not
                     any(can_assign(n,y) for y in dat.sites) and
                     dat.sites[n]["Demand"] > 0]
    if unassignables:
        print(f"unassignables {unassignables}")
        return

    full_parameters = input_schema.create_full_parameters_dict(dat)

    m = gu.Model("cog", env=gurobi_env())
    m.setParam("LogToConsole", full_parameters["Gurobi LogToConsole"])

    assign_vars = {(n, assigned_to) : m.addVar(vtype = gu.GRB.BINARY,
                                        name = "%s_%s"%(n,assigned_to),
                                        obj = get_distance(n,assigned_to) *
                                              dat.sites[n]["Demand"])
                    for n in dat.sites for assigned_to in dat.sites
                    if can_assign(n, assigned_to)}
    open_vars = {n : m.addVar(vtype = gu.GRB.BINARY, name = "open_%s"%n)
                     for n in dat.sites
                     if dat.sites[n]["Center Status"] == "Can Be Center"}
    if not open_vars:
        print("Nothing can be a center!\n") # Infeasibility detected.
        return


    # using ticdat.Slicer instead of tuplelist simply as a matter of taste/vanity
    assign_slicer = Slicer(assign_vars)

    for n, r in dat.sites.items():
        if r["Demand"] > 0:
            m.addConstr(gu.quicksum(assign_vars[n, assign_to]
                                    for _, assign_to in assign_slicer.slice(n, "*"))
                        == 1,
                        name = "must_assign_%s"%n)

    crippledfordemo = full_parameters["Formulation"] == "Weak"
    for assigned_to, r in dat.sites.items():
        if r["Center Status"] == "Can Be Center":
            _assign_vars = [assign_vars[n, assigned_to]
                            for n,_ in assign_slicer.slice("*", assigned_to)]
            if crippledfordemo:
                m.addConstr(gu.quicksum(_assign_vars) <=
                            len(_assign_vars) * open_vars[assigned_to],
                            name="weak_force_open%s"%assigned_to)
            else:
                for var in _assign_vars :
                    m.addConstr(var <= open_vars[assigned_to],
                                name = "strong_force_open_%s"%assigned_to)

    number_of_centroids = number_centroids

    m.addConstr(gu.quicksum(v for v in open_vars.values()) == number_of_centroids,
                name= "numCentroids")

    m.Params.MIPGap = full_parameters["MIP Gap"]

    return m, assign_vars, open_vars

def _populate_sln(sln, k, m, assign_vars, open_vars):
    if not hasattr(m, "status"):
        print("missing status - likely premature termination")
        return
    for failStr,grbkey in (("inf_or_unbd", gu.GRB.INF_OR_UNBD),
                           ("infeasible", gu.GRB.INFEASIBLE),
                           ("unbounded", gu.GRB.UNBOUNDED)):
         if m.status == grbkey:
            print("Optimization failed due to model status of %s"%failStr)
            return

    if m.status == gu.GRB.INTERRUPTED:
        if not all(hasattr(var, "x") for var in open_vars.values()):
            print("No solution was found\n")
            return
    elif m.status != gu.GRB.OPTIMAL:
        print("unexpected status %s\n" % m.status)
        return

    sln.parameters[k, "Lower Bound"] = getattr(m, "objBound", m.objVal)
    sln.parameters[k, "Upper Bound"] = m.objVal

    def almostone(x) :
        return abs(x-1) < 0.0001

    for (n, assigned_to), var in assign_vars.items() :
        if almostone(var.x) :
            sln.assignments[k, n, assigned_to] = {}
    for n,var in open_vars.items() :
        if almostone(var.x) :
            sln.openings[k, n]={}

def solve(dat, diagnostic_log=None):
    assert input_schema.good_tic_dat_object(dat)
    assert not input_schema.find_foreign_key_failures(dat)
    assert not input_schema.find_data_type_failures(dat)
    assert not input_schema.find_data_row_failures(dat)
    start = time.time()
    diagnostic_log = diagnostic_log or LogFile(None)

    full_parameters = input_schema.create_full_parameters_dict(dat)

    models = {k: _make_model_dat(dat, v["Number of Centroids"]) for k, v in dat.solutions.items()}
    if not all(v for v in models.values()) or len(models) == 0:
        return

    if full_parameters["Threading"] == "Enabled":
        for m, _, __ in models.values():
            m.setParam("Threads", 1)
        def task(id):
            models[id][0].optimize() # this will escape the Python GIL on local solves because Gurobi kernel is C code
        threads = []
        for k in models:
            t = Thread(target=task, args=(k,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
    else:
        for m, _, __ in models.values():
            m.optimize()

    sln = solution_schema.TicDat()
    for k, (m, assign_vars, open_vars) in models.items():
        _populate_sln(sln, k, m, assign_vars, open_vars)

    for _ in [print, diagnostic_log.write]:
        _(f"Run Time : {time.time() - start}")

    return sln if sln.parameters else None
# ---------------------------------------------------------------------------------


# when run from the command line, will read/write json/xls/csv/db/mdb files
if __name__ == "__main__":
    standard_main(input_schema, solution_schema, solve)
# ---------------------------------------------------------------------------------




