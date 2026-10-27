from labdex.generators import dns, hostfiles, opnsense
from labdex.inventory import export_yaml
from labdex.plugins.base import ExportFile, Plugin as Base


class Plugin(Base):
    def export_opnsense_json(self, params):
        return ExportFile("opnsense-aliases.json", "application/json", opnsense.export_json())

    def export_dns_zone(self, params):
        return ExportFile("records.zone", "text/plain", dns.bind())

    def export_dns_json(self, params):
        return ExportFile("records.json", "application/json", dns.as_json())

    def export_ansible_inventory(self, params):
        from labdex.ansiblevault import VaultError
        from labdex.plugins.base import PluginError
        try:
            text = hostfiles.ansible_yaml(self.settings.get("vault_password") or "", self.settings.get("vault_id") or "")
        except VaultError as exc:
            raise PluginError(str(exc)) from exc
        return ExportFile("hosts.yaml", "application/yaml", text)

    def export_dhcp_csv(self, params):
        return ExportFile("dhcp-reservations.csv", "text/csv", hostfiles.dhcp_csv())

    def export_inventory_yaml(self, params):
        return ExportFile("inventory.yaml", "application/yaml", export_yaml())
