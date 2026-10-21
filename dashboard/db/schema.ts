import { sqliteTable, text, integer, primaryKey, index } from "drizzle-orm/sqlite-core";

export const snapshots = sqliteTable("snapshots", {
  id: text("id").primaryKey(),
  generatedMs: integer("generated_ms").notNull(),
  receivedMs: integer("received_ms").notNull(),
  payload: text("payload").notNull(),
});
export const orders = sqliteTable("orders", {
  id: text("id").primaryKey(),
  section: text("section").notNull(),
  updatedMs: integer("updated_ms").notNull(),
  payload: text("payload").notNull(),
}, (table) => [index("orders_section_updated").on(table.section, table.updatedMs, table.id)]);
export const accounts = sqliteTable("accounts", {
  id: text("id").primaryKey(),
  label: text("label").notNull(),
  envelope: text("envelope").notNull(),
  active: integer("active").notNull().default(1),
  updatedMs: integer("updated_ms").notNull(),
});
export const pnlSamples = sqliteTable("pnl_samples", {
  strategyId: text("strategy_id").notNull(),
  bucket: integer("bucket").notNull(),
  payload: text("payload").notNull(),
}, (table) => [primaryKey({ columns: [table.strategyId, table.bucket] })]);
