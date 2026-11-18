"""Backend interface: what the orchestrator asks of the infrastructure."""


class Backend:
    name = "base"
    simulated = False

    def provision(self, desc):
        """Creates/reserves the resources. Returns the node inventory (list of dicts)."""
        raise NotImplementedError

    def apply_impairments(self, cfg):
        """Applies the impairments of the configuration. Returns the log of the commands."""
        raise NotImplementedError

    def deploy(self, desc, cfg):
        """Distribuisce i componenti secondo il placement di cfg."""
        raise NotImplementedError

    def run_workload(self, desc, cfg, seed):
        """Esegue il workload. Ritorna (samples, status), samples = [(t_s, metric, value, tier)]."""
        raise NotImplementedError

    def teardown(self):
        raise NotImplementedError
