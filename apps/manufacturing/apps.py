from django.apps import AppConfig


class ManufacturingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.manufacturing"
    label = "manufacturing"

    def ready(self):
        from apps.hr.attendance import register_shift_clock
        from apps.hr.payroll import register_piece_measure
        from apps.inventory.models import register_unit_provider
        from apps.inventory.costing import register_pooled_move
        from apps.inventory.tracking import register_lot_commitment
        from apps.quality.release import register_lot_decided, register_lot_sources
        from apps.sales.models import register_material_checker, register_ownership_checker

        from .compliance import material_problems
        from .conversion import bags_converted, floor_checks_decided, liners_sealed
        from .inward import ownership_problems
        from .piecework import kilograms_woven, metres_woven
        from .rebatch import made_into, pooled_into, sources
        from .shifts import shift_starts
        from .tape_loads import creel_promises
        from .trace import held_inputs, made_into_by_runs
        from .woven import sack_units

        register_unit_provider(sack_units)
        # What a rebatch took went into the batches it made: followed there (seen #9).
        register_pooled_move(pooled_into)
        # Tape on a backflushed run's creels is on the shelf and not free (O133).
        register_lot_commitment(creel_promises)
        register_lot_sources(sources, made_into)
        # Production is a lineage too: a recall follows the runs, as the trace does (O207).
        register_lot_sources(held_inputs, made_into_by_runs)
        # A bundle decided closes the to-dos on the off roll and coating it came from (review F8).
        register_lot_decided(floor_checks_decided)
        register_material_checker(material_problems)
        register_ownership_checker(ownership_problems)
        register_piece_measure("metres_woven", "Metres woven", metres_woven)
        register_piece_measure("kilograms_woven", "Kilograms woven", kilograms_woven)
        register_piece_measure("bags_converted", "Bags cut and stitched", bags_converted)
        register_piece_measure("liners_sealed", "Liners cut and sealed", liners_sealed)
        register_shift_clock(shift_starts)
