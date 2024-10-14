# Mega Media Helm Chart

_A single chart to handle your media center related deployments to a single node_

**Status: In Development**

## Why??

Under most homelab storage scenarios, it is vastly more performant (and more likely to be the only option)
to have your media storage use `ReadWriteOnce`, which will require all pods to be running on the same system.

Therefore, this helm chart provides everything needed for a great media system while working under the assumption
everything needs to run off the same system! We know this is really node ideal (trust us, we really do) but we really
want those sweet, sweet r/w speeds.
