"""
load_distribution.py
----------------------
Load Distribution SIMULATION across multiple worker nodes.

IMPORTANT - what this is and what it is NOT:
    This module does NOT spin up actual separate servers/processes.
    It simulates, within a single Django process, how incoming batch
    sub-tasks WOULD be distributed across N worker nodes using a
    Round-Robin scheduling strategy, and records which simulated node
    each sub-task was assigned to.

    This is the standard, honest interpretation of "simulate load
    distribution across servers" for a project of this scope: building
    actual multi-server infrastructure is out of scope, but the
    SCHEDULING ALGORITHM itself (Round-Robin) is implemented for real
    and is fully testable.

Why Round-Robin was chosen (justification, as required by the report):
    - Round-Robin is the simplest fair-distribution algorithm: each
      node receives tasks in strict rotation, guaranteeing that no
      single node is ever assigned two tasks before every other node
      has received one.
    - It requires no knowledge of task duration or node load in
      advance, unlike smarter strategies (Least-Connections, Weighted
      Round-Robin), which makes it the right baseline choice for a
      batch of short, roughly-uniform sub-tasks (processing one order
      at a time), as is the case in process_sales_batch_task.
    - It is O(1) per assignment (a single modulo operation), adding
      negligible overhead to the batch processing pipeline.

Where this is used:
    Called from shop.tasks.process_sales_batch_task to decide, for
    logging/reporting purposes, which simulated worker node "handles"
    each order within a batch. The actual order processing still runs
    on the single real Celery worker process; only the NODE LABEL is
    distributed via Round-Robin, to demonstrate and test the
    scheduling logic itself in isolation from real infrastructure.
"""

import logging

logger = logging.getLogger("load_distribution")

SIMULATED_NODES = ["worker-node-1", "worker-node-2", "worker-node-3"]


class RoundRobinScheduler:
    """
    A real, testable Round-Robin scheduler.

    Assigned_Node(i) = SIMULATED_NODES[i mod len(SIMULATED_NODES)]

    This matches the mathematical definition of Round-Robin scheduling:
    the i-th task is assigned to node (i mod N), where N is the number
    of available nodes.
    """

    def __init__(self, nodes=None):
        self.nodes = nodes if nodes is not None else SIMULATED_NODES
        self._counter = 0

    def next_node(self):
        """Returns the next node in rotation and advances the counter."""
        node = self.nodes[self._counter % len(self.nodes)]
        self._counter += 1
        return node

    def reset(self):
        self._counter = 0


def distribute_batch(items, scheduler=None):
    """
    Distributes a list of items (e.g. unprocessed orders) across the
    simulated nodes using Round-Robin, and returns a list of
    (item, assigned_node) tuples.

    This is the function actually called by process_sales_batch_task.
    """
    scheduler = scheduler or RoundRobinScheduler()
    assignments = []
    for item in items:
        node = scheduler.next_node()
        assignments.append((item, node))
        logger.info("[LoadDistribution] item=%s -> %s", getattr(item, "id", item), node)
    return assignments


def distribution_summary(assignments):
    """
    Returns a dict of {node_name: count} summarizing how many items
    were assigned to each node - useful for the batch dashboard and
    for proving the distribution is actually balanced.
    """
    summary = {}
    for _, node in assignments:
        summary[node] = summary.get(node, 0) + 1
    return summary