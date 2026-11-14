"""TF-card library backend for the Armada Control Decky plugin.

The modules in this package (``service``, ``tf_card``, ``tf_inspection``,
``tf_state`` and ``tf_worker``) are the TF backend migrated, behaviour
unchanged, from the mydanyi/armada-installer plugin at commit
1ac40b959085e6b55c2aef1467080b07560a2431, which is distributed under
GPL-3.0-or-later.  The copyright stays with the original author; the migrated
code is distributed here as part of this GPL-2.0 project with the copyright
holder's permission.

The fixed runtime paths, the durable checkpoint and the
``armada-tf-library.service`` unit name are retained from the source plugin so
both share the same job state, and a conversion can be recovered after a
restart.  Isolation rests on the existing lock file and unit-state checks only;
this package does not guarantee that two conversions can never run in parallel.
"""
