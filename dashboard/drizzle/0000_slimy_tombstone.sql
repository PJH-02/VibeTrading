CREATE TABLE `accounts` (
	`id` text PRIMARY KEY NOT NULL,
	`label` text NOT NULL,
	`envelope` text NOT NULL,
	`active` integer DEFAULT 1 NOT NULL,
	`updated_ms` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `orders` (
	`id` text PRIMARY KEY NOT NULL,
	`section` text NOT NULL,
	`updated_ms` integer NOT NULL,
	`payload` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `orders_section_updated` ON `orders` (`section`,`updated_ms`,`id`);--> statement-breakpoint
CREATE TABLE `pnl_samples` (
	`strategy_id` text NOT NULL,
	`bucket` integer NOT NULL,
	`payload` text NOT NULL,
	PRIMARY KEY(`strategy_id`, `bucket`)
);
--> statement-breakpoint
CREATE TABLE `snapshots` (
	`id` text PRIMARY KEY NOT NULL,
	`generated_ms` integer NOT NULL,
	`received_ms` integer NOT NULL,
	`payload` text NOT NULL
);
