# Change sets

A change set is a batch of inventory changes you review as one unit: "move the guests to a new
VLAN" is two networks and six hosts, and you want to see what it does before any of it is saved.

Open **Automation › Change sets**, or keep the file in git and use the command line:

```sh
labdex changeset guest-vlan.yaml            # preview: nothing is kept
labdex changeset guest-vlan.yaml --apply    # all of it, or none
```

## The file

```yaml
networks:
  - {name: guest2, vlan: 232, zone: CA_UT, prefix4: 10.10.232.0/24, gateway4: 10.10.232.254}
hosts:
  - name: cli-lt-ana-01
    zone: CA_UT
    interfaces: [{network: guest2, ip4: 10.10.232.5, primary: true}]
aliases:
  - {name: GUEST_TWO_HOSTS, type: host, members: [cli-lt-ana-01]}
delete:
  hosts: [old-vm-test-01]
  networks: [guest]
  aliases: []
```

An entry that exists is **updated**: only the keys you give change, so `{name: nas, function: Backup}`
leaves everything else on `nas` alone. An entry that doesn't exist is **created**. Order doesn't matter:
networks come first, then hosts, then aliases, and deletions run hosts, aliases, networks, so a set can
retire a network together with the hosts on it.

## Preview, then apply

*Preview* applies the file inside a transaction and rolls it back. The report lists every change (and
the fields that change on updates), the **lint findings it adds or resolves**, the **firewall aliases**
that appear, vanish or change, and the **DNS records** added or removed.

*Apply* checks everything again, since the inventory may have changed since you previewed, and saves it
only if every change passes. One refused change (an address outside its network, a network still in use,
a zone you may not edit) saves nothing. Each changed object gets a History entry named after the set, so
a bad set can be undone object by object from History. An applied set is kept as a record and can't be
applied twice.

You need the right to edit each object a set touches; a set never gives you more access than editing
the same things by hand.
