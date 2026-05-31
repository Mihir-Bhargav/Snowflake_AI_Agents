# ADR 0001 — Microsoft Fabric as the single data platform

- **Status:** Accepted
- **Date:** 2026-05-30
- **Deciders:** Project sponsor (Mihir)

## Context
The project codename is "Snowflake_AI_Agents," which implies Snowflake as the warehouse.
However, the intended data lake, staging area, and tool plane are all Microsoft Fabric
(OneLake) accessed via the **Fabric Remote MCP**. Running two competing platforms
(Snowflake + Fabric) would split governance, double cost, and create brittle cross-platform
data movement for no functional gain at this stage.

## Decision
Use **Microsoft Fabric / OneLake** as the single source of truth for all layers
(Bronze → Silver → Gold) and **Power BI** for the executive presentation layer.
"Snowflake" is retained only as the project codename, not as a technology.

## Consequences
- **Positive:** one governance, security, and lineage model; compute lives next to data
  (Fabric notebooks/Spark); native Power BI semantic model for dashboards; a single MCP
  seam (Fabric Remote) for all agent data operations → simpler, auditable, testable.
- **Negative / watch-items:** Fabric capacity sizing must cover Spark + SQL endpoint +
  Power BI refresh load; team must be comfortable with Delta/OneLake conventions.
- **Reversibility:** medallion layers are platform-portable (Delta/Parquet). If Snowflake
  is ever mandated, Gold marts can be mirrored without redesigning the agent layer, since
  agents talk only to the MCP tool plane, not to storage directly.
