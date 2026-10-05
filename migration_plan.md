Alembic handles application schema.
Logical replication handles existing data + ongoing changes.
validation scripts handle sequences, counts, constraints, indexes and integrity.
Cutover happens only after OVH has caught up.


     
     
     
     CURRENT PRODUCTION
           │
           ▼
  Supabase PostgreSQL 17
  Publisher / WAL source
           │
  Logical Replication
  Initial snapshot + CDC
           │
           ▼
   OVH PostgreSQL 17
       Subscriber
           │
  continuously catches up
           │
           ▼
    Validation phase
           │
  replication lag ≈ 0
           │
           ▼
  Very short write freeze
           │
  final consistency check
           │
           ▼
    FastAPI cutover


                        ┌─────────────────────┐
                        │ Alembic migrations  │
                        └──────────┬──────────┘
                                   │
                         Build application
                         schema on target
                                   │
                                   ▼
Supabase ───── Logical replication ─────► OVH PostgreSQL
   │                                         │
   │ schema                                  │ schema
   │ data                                    │ data
   │ sequences                               │ sequences
   ▼                                         ▼
         Compare / validate / reconcile