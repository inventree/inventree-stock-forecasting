"""A complex, hand-built multi-level BOM fixture for forecasting regression tests.

This module only constructs the dataset and checks that it was built correctly.
The actual forecasting assertions (what `PartForecast` should compute against this
data) are deliberately deferred to a follow-up test module, once the fixture shape
below has been reviewed.

Structure
---------

Components (tier 0, purchaseable, not assemblies)::

    C1   C2   C3   C4      (C4 is ALSO salable directly)

Sub-assemblies (tier 1)::

    M1 = 2x C1 + 1x C2
    M2 = 3x C2 + 1x C3      (M2 is ALSO purchaseable directly)
    M3 = 2x C3 + 4x C4
    M4 = 1x C1 + 2x C4      (orphan - not used by any tier 2 module)

Modules (tier 2)::

    N1 = 2x M1 + 1x M2      (N1 is ALSO purchaseable directly)
    N2 = 1x M2 + 3x M3      (N2 is ALSO salable directly)
    N3 = 2x M3 + 1x M4      (orphan - not used by any tier 3 product)

Products (tier 3)::

    TOP1 = 1x N1 + 2x N2    <- diamond: reaches M2 via N1 AND via N2
    TOP2 = 3x N1            <- shares N1 with TOP1 (also purchaseable directly)

So the full graph is 4 tiers deep (C -> M -> N -> TOP), TOP1 has a genuine
"diamond" dependency on M2 (via two independent paths), N1 is reused across
both top-level products, and M4/N3 are "dead-end" intermediates that exist
(with their own BOMs and orders) but never propagate up to a top-level product.

On top of the BOM structure, a richer set of existing orders is created:

- Multiple build orders, at varying levels of completion, against both
  top-level products (TOP1 x2, TOP2) and mid-tier assemblies (N1, N2, M2, M4,
  N3 - including the two orphan parts).
- Multiple sales orders, at varying levels of completion (some pending, some
  partially shipped), against both top-level products (TOP1 x2, TOP2 x2) and
  the parts which are salable further down the chain (N2, C4).
- One further sales order against TOP1 whose SAME line item (part + quantity)
  is repeated 5x, each with a different target_date a month apart into the
  future - exercising multiple SalesOrderLineItems against a single SalesOrder.
- Multiple purchase orders, at varying levels of completion, against
  bottom-level components (C1, C3) and the parts which are purchaseable
  further up the chain (M2 x2, N1 x2, TOP2).

Existing stock is seeded across every tier with a deliberate mix::

    Sufficient stock (comfortably covers outstanding demand): C1, M2, N1
    Insufficient stock (some, but not enough):                C3, C4, M3, N2, TOP2
    No stock at all (no StockItem created):                   C2, M1, M4, N3, TOP1

Every tier has at least one part with insufficient stock, and every tier except
the top has a part with clearly-sufficient stock. Both orphan parts (M4, N3) and
TOP1 have no stock at all, so their outstanding demand has no cushion to offset
against.

Each top-level product (TOP1, TOP2) gets a full year's forecast timeline
staged on top of its near-term orders: 7 build orders and 5 sales orders each,
spread across the next ~360 days at varying quantities and completion/shipped
levels.

Finally, a separate, deliberately isolated fixture exercises *inherited* BOM
items across part variants - a code path (`BomItem.inherited`, `Part.variant_of`)
the rest of the graph above never touches::

    TEMPLATE (is_template=True) = 1x SHARED (inherited=True)
    VARIANT_A, VARIANT_B (variant_of=TEMPLATE)

`SHARED` is not connected to the rest of the fixture. Because the BomItem is
marked `inherited`, both `VARIANT_A` and `VARIANT_B` effectively include
`SHARED` in their BOM too, without it being redefined per-variant. A sales
order is placed against `VARIANT_A` only, to check that querying the
*template's* own forecast with `include_variants=True` picks it up.

Finally, a 10-tier-deep chain is grafted onto TOP1, to stress-test the
upstream traversal's depth, width, and multi-path handling well beyond the
4-tier diamond above::

    D0  D0B  D0C                         (tier 0 - leaves)
    D1  = 2x D0  + 1x D0B                 (tier 1)
    D2  = 1x D1  + 3x D0C                 (tier 2, also salable)
    D3  = 2x D2                           (tier 3)
    D4  = 1x D3                           (tier 4, has its own build order)
    D5  = 3x D4                           (tier 5)
    D5B = 2x D4                           (tier 5 - sibling, shares D4 with D5)
    D6  = 1x D5  + 1x D5B                 (tier 6 - widened BOM)
    D7  = 2x D6  + 1x D5                  (tier 7 - shortcut: D5 direct AND via D6)
    D8  = 1x D7                           (tier 8)
    TOP1 gains: + 1x D8 (completes the 10-tier chain: D0 -> ... -> D8 -> TOP1)
                + 5x D0 (shortcut: tier 0 straight to tier 9)

D0 is reachable from TOP1 both via the full 9-level chain and via a direct
BOM line, and D4 is reachable from D7 via two internal diamonds (through D5
and D5B, which both merge again at D6) - multi-path shortcuts at very
different scales, layered on top of the existing rich order data on TOP1.
"""

from datetime import date, timedelta

from build.models import Build
from company.models import Company, SupplierPart
from order.models import (
    PurchaseOrder,
    PurchaseOrderLineItem,
    SalesOrder,
    SalesOrderLineItem,
)
from part.models import BomItem, Part
from stock.models import StockItem

from InvenTree.unit_test import InvenTreeTestCase


class MultiLevelBOMTestCase(InvenTreeTestCase):
    """Builds a complex, 4-tier BOM graph with diamond dependencies, shared and
    orphaned intermediate assemblies, and a rich mix of existing build/sales/
    purchase orders at varying levels of completion.
    """

    @classmethod
    def setUpTestData(cls):
        """Construct the full part/BOM/order graph once for the whole test class."""
        super().setUpTestData()

        cls.supplier = Company.objects.create(name='Multi-Level Supplier', is_supplier=True)
        cls.customer = Company.objects.create(name='Multi-Level Customer', is_customer=True)

        cls._create_parts()
        cls._create_bom_items()
        cls._create_purchase_orders()
        cls._create_sales_orders()
        cls._create_build_orders()
        cls._create_stock_items()
        cls._create_future_orders_for_top_level_parts()
        cls._create_template_variant_fixture()
        cls._create_deep_chain_fixture()

    @classmethod
    def _create_parts(cls):
        """Create all parts across all four tiers, including two orphan intermediates."""
        # Tier 0: bottom-level components - purchaseable, not assemblies
        cls.c1 = Part.objects.create(
            name='Component 1', description='Tier 0 component',
            purchaseable=True, component=True, assembly=False, salable=False,
        )
        cls.c2 = Part.objects.create(
            name='Component 2', description='Tier 0 component',
            purchaseable=True, component=True, assembly=False, salable=False,
        )
        cls.c3 = Part.objects.create(
            name='Component 3', description='Tier 0 component',
            purchaseable=True, component=True, assembly=False, salable=False,
        )
        cls.c4 = Part.objects.create(
            name='Component 4', description='Tier 0 component (also salable)',
            purchaseable=True, component=True, assembly=False, salable=True,
        )

        # Tier 1: sub-assemblies built from components
        cls.m1 = Part.objects.create(
            name='Sub-Assembly 1', description='Tier 1 sub-assembly',
            assembly=True, component=True, purchaseable=False, salable=False,
        )
        cls.m2 = Part.objects.create(
            name='Sub-Assembly 2', description='Tier 1 sub-assembly (also purchaseable)',
            assembly=True, component=True, purchaseable=True, salable=False,
        )
        cls.m3 = Part.objects.create(
            name='Sub-Assembly 3', description='Tier 1 sub-assembly',
            assembly=True, component=True, purchaseable=False, salable=False,
        )
        cls.m4 = Part.objects.create(
            name='Sub-Assembly 4', description='Tier 1 sub-assembly (orphan - not used further up)',
            assembly=True, component=True, purchaseable=False, salable=False,
        )

        # Tier 2: modules built from sub-assemblies
        cls.n1 = Part.objects.create(
            name='Module 1', description='Tier 2 module (also purchaseable)',
            assembly=True, component=True, purchaseable=True, salable=False,
        )
        cls.n2 = Part.objects.create(
            name='Module 2', description='Tier 2 module (also salable)',
            assembly=True, component=True, purchaseable=False, salable=True,
        )
        cls.n3 = Part.objects.create(
            name='Module 3', description='Tier 2 module (orphan - not used by any top-level product)',
            assembly=True, component=True, purchaseable=False, salable=False,
        )

        # Tier 3: top-level products built from modules
        cls.top1 = Part.objects.create(
            name='Product 1', description='Tier 3 top-level product',
            assembly=True, component=False, purchaseable=False, salable=True,
        )
        cls.top2 = Part.objects.create(
            name='Product 2', description='Tier 3 top-level product (also purchaseable)',
            assembly=True, component=False, purchaseable=True, salable=True,
        )

        cls.all_parts = [
            cls.c1, cls.c2, cls.c3, cls.c4,
            cls.m1, cls.m2, cls.m3, cls.m4,
            cls.n1, cls.n2, cls.n3,
            cls.top1, cls.top2,
        ]

        # MPTT `tree_id` is only finalized once all sibling root Parts have been
        # created - a later Part's insert can shift an earlier one's `tree_id`,
        # tripping BomItem's "recursive" guard on stale in-memory values. Refresh
        # everything now, after all Parts exist, before wiring up any BomItems.
        for part in cls.all_parts:
            part.refresh_from_db()

    @classmethod
    def _create_bom_items(cls):
        """Wire up the BOM structure across all tiers, including the two orphan branches."""
        # Tier 1 BOMs
        BomItem.objects.create(part=cls.m1, sub_part=cls.c1, quantity=2)
        BomItem.objects.create(part=cls.m1, sub_part=cls.c2, quantity=1)

        BomItem.objects.create(part=cls.m2, sub_part=cls.c2, quantity=3)
        BomItem.objects.create(part=cls.m2, sub_part=cls.c3, quantity=1)

        BomItem.objects.create(part=cls.m3, sub_part=cls.c3, quantity=2)
        BomItem.objects.create(part=cls.m3, sub_part=cls.c4, quantity=4)

        # M4 is an "orphan" sub-assembly - it has its own BOM, but (unlike M1-M3)
        # is never referenced as a sub_part by any tier 2 module.
        BomItem.objects.create(part=cls.m4, sub_part=cls.c1, quantity=1)
        BomItem.objects.create(part=cls.m4, sub_part=cls.c4, quantity=2)

        # Tier 2 BOMs - M2 is shared between N1 and N2 (sets up the diamond)
        BomItem.objects.create(part=cls.n1, sub_part=cls.m1, quantity=2)
        cls.n1_m2_item = BomItem.objects.create(part=cls.n1, sub_part=cls.m2, quantity=1)

        cls.n2_m2_item = BomItem.objects.create(part=cls.n2, sub_part=cls.m2, quantity=1)
        BomItem.objects.create(part=cls.n2, sub_part=cls.m3, quantity=3)

        # N3 is an "orphan" module - it shares M3 with N2 (further reuse, but not
        # a diamond, since N3 itself never reaches a top-level product), and also
        # pulls in the orphan M4. Unlike N1/N2, N3 is never used by TOP1/TOP2.
        BomItem.objects.create(part=cls.n3, sub_part=cls.m3, quantity=2)
        BomItem.objects.create(part=cls.n3, sub_part=cls.m4, quantity=1)

        # Tier 3 BOMs - N1 is shared between TOP1 and TOP2
        cls.top1_n1_item = BomItem.objects.create(part=cls.top1, sub_part=cls.n1, quantity=1)
        cls.top1_n2_item = BomItem.objects.create(part=cls.top1, sub_part=cls.n2, quantity=2)

        cls.top2_n1_item = BomItem.objects.create(part=cls.top2, sub_part=cls.n1, quantity=3)

    @classmethod
    def _create_purchase_orders(cls):
        """Create incoming purchase orders, at varying levels of completion, against
        bottom-level components and the parts which are purchaseable further up the chain.
        """
        cls.sp_c1 = SupplierPart.objects.create(part=cls.c1, supplier=cls.supplier, SKU='SKU-C1')
        cls.sp_c3 = SupplierPart.objects.create(part=cls.c3, supplier=cls.supplier, SKU='SKU-C3')
        cls.sp_m2 = SupplierPart.objects.create(part=cls.m2, supplier=cls.supplier, SKU='SKU-M2')
        cls.sp_n1 = SupplierPart.objects.create(part=cls.n1, supplier=cls.supplier, SKU='SKU-N1')
        cls.sp_top2 = SupplierPart.objects.create(part=cls.top2, supplier=cls.supplier, SKU='SKU-TOP2')

        # Bottom-level components
        cls.po_c1 = PurchaseOrder.objects.create(supplier=cls.supplier, reference='PO-ML-001')
        cls.po_c1_line = PurchaseOrderLineItem.objects.create(
            order=cls.po_c1, part=cls.sp_c1, quantity=200,
            target_date=date.today() + timedelta(days=7),
        )

        cls.po_c3 = PurchaseOrder.objects.create(supplier=cls.supplier, reference='PO-ML-002')
        cls.po_c3_line = PurchaseOrderLineItem.objects.create(
            order=cls.po_c3, part=cls.sp_c3, quantity=80, received=20,
            target_date=date.today() + timedelta(days=10),
        )

        # M2 (tier 1, purchaseable) - two POs at different levels of completion
        cls.po_m2_a = PurchaseOrder.objects.create(supplier=cls.supplier, reference='PO-ML-003')
        cls.po_m2_a_line = PurchaseOrderLineItem.objects.create(
            order=cls.po_m2_a, part=cls.sp_m2, quantity=25,
            target_date=date.today() + timedelta(days=14),
        )

        cls.po_m2_b = PurchaseOrder.objects.create(supplier=cls.supplier, reference='PO-ML-004')
        cls.po_m2_b_line = PurchaseOrderLineItem.objects.create(
            order=cls.po_m2_b, part=cls.sp_m2, quantity=10, received=8,
            target_date=date.today() + timedelta(days=3),
        )

        # N1 (tier 2, purchaseable) - two POs at different levels of completion
        cls.po_n1_a = PurchaseOrder.objects.create(supplier=cls.supplier, reference='PO-ML-005')
        cls.po_n1_a_line = PurchaseOrderLineItem.objects.create(
            order=cls.po_n1_a, part=cls.sp_n1, quantity=15,
            target_date=date.today() + timedelta(days=20),
        )

        cls.po_n1_b = PurchaseOrder.objects.create(supplier=cls.supplier, reference='PO-ML-006')
        cls.po_n1_b_line = PurchaseOrderLineItem.objects.create(
            order=cls.po_n1_b, part=cls.sp_n1, quantity=20, received=12,
            target_date=date.today() + timedelta(days=6),
        )

        # TOP2 (tier 3, purchaseable)
        cls.po_top2 = PurchaseOrder.objects.create(supplier=cls.supplier, reference='PO-ML-007')
        cls.po_top2_line = PurchaseOrderLineItem.objects.create(
            order=cls.po_top2, part=cls.sp_top2, quantity=5,
            target_date=date.today() + timedelta(days=28),
        )

    @classmethod
    def _create_sales_orders(cls):
        """Create open sales orders, at varying levels of completion, against both
        top-level products and the parts which are salable further down the chain.
        """
        # TOP1 (tier 3) - two SOs, one pending and one partially shipped
        cls.so_top1_a = SalesOrder.objects.create(customer=cls.customer, reference='SO-ML-001')
        cls.so_top1_a_line = SalesOrderLineItem.objects.create(
            order=cls.so_top1_a, part=cls.top1, quantity=4,
            target_date=date.today() + timedelta(days=9),
        )

        cls.so_top1_b = SalesOrder.objects.create(customer=cls.customer, reference='SO-ML-002')
        cls.so_top1_b_line = SalesOrderLineItem.objects.create(
            order=cls.so_top1_b, part=cls.top1, quantity=8, shipped=3,
            target_date=date.today() + timedelta(days=21),
        )

        # TOP2 (tier 3) - two SOs, one pending and one partially shipped
        cls.so_top2_a = SalesOrder.objects.create(customer=cls.customer, reference='SO-ML-003')
        cls.so_top2_a_line = SalesOrderLineItem.objects.create(
            order=cls.so_top2_a, part=cls.top2, quantity=10,
            target_date=date.today() + timedelta(days=25),
        )

        cls.so_top2_b = SalesOrder.objects.create(customer=cls.customer, reference='SO-ML-004')
        cls.so_top2_b_line = SalesOrderLineItem.objects.create(
            order=cls.so_top2_b, part=cls.top2, quantity=6, shipped=1,
            target_date=date.today() + timedelta(days=21),
        )

        # N2 (tier 2, salable)
        cls.so_n2 = SalesOrder.objects.create(customer=cls.customer, reference='SO-ML-005')
        cls.so_n2_line = SalesOrderLineItem.objects.create(
            order=cls.so_n2, part=cls.n2, quantity=12, shipped=4,
            target_date=date.today() + timedelta(days=14),
        )

        # C4 (tier 0, salable)
        cls.so_c4 = SalesOrder.objects.create(customer=cls.customer, reference='SO-ML-006')
        cls.so_c4_line = SalesOrderLineItem.objects.create(
            order=cls.so_c4, part=cls.c4, quantity=50,
            target_date=date.today() + timedelta(days=5),
        )

        # TOP1 (tier 3) - a single order with the SAME line item (part + quantity)
        # repeated 5x, each with a different target_date a month apart into the
        # future. Exercises multiple SalesOrderLineItems against one SalesOrder.
        cls.so_top1_repeat = SalesOrder.objects.create(
            customer=cls.customer, reference='SO-ML-007'
        )
        cls.so_top1_repeat_lines = [
            SalesOrderLineItem.objects.create(
                order=cls.so_top1_repeat, part=cls.top1, quantity=7,
                target_date=date.today() + timedelta(days=35 + 30 * i),
            )
            for i in range(5)
        ]

    @classmethod
    def _create_build_orders(cls):
        """Create active build orders, at varying levels of completion, against both
        top-level products and mid-tier assemblies - including the two orphan parts.
        """
        # TOP1 (tier 3) - two builds, one just started and one nearly complete
        cls.build_top1_a = Build.objects.create(
            part=cls.top1, quantity=10, completed=2, reference='BO-9001',
            target_date=date.today() + timedelta(days=14),
        )
        cls.build_top1_b = Build.objects.create(
            part=cls.top1, quantity=5, completed=0, reference='BO-9002',
            target_date=date.today() + timedelta(days=25),
        )

        # TOP2 (tier 3) - nearly complete
        cls.build_top2 = Build.objects.create(
            part=cls.top2, quantity=8, completed=6, reference='BO-9003',
            target_date=date.today() + timedelta(days=4),
        )

        # N1 (tier 2)
        cls.build_n1 = Build.objects.create(
            part=cls.n1, quantity=20, completed=5, reference='BO-9004',
            target_date=date.today() + timedelta(days=10),
        )

        # N2 (tier 2) - nearly complete
        cls.build_n2 = Build.objects.create(
            part=cls.n2, quantity=12, completed=10, reference='BO-9005',
            target_date=date.today() + timedelta(days=2),
        )

        # N3 (tier 2, orphan)
        cls.build_n3 = Build.objects.create(
            part=cls.n3, quantity=4, completed=1, reference='BO-9006',
            target_date=date.today() + timedelta(days=8),
        )

        # M2 (tier 1) - just started
        cls.build_m2 = Build.objects.create(
            part=cls.m2, quantity=15, completed=0, reference='BO-9007',
            target_date=date.today() + timedelta(days=5),
        )

        # M4 (tier 1, orphan) - just started
        cls.build_m4 = Build.objects.create(
            part=cls.m4, quantity=6, completed=0, reference='BO-9008',
            target_date=date.today() + timedelta(days=12),
        )

    @classmethod
    def _create_stock_items(cls):
        """Seed existing stock across every tier with a deliberate mix: some parts
        with enough stock to cover outstanding demand, some with too little, and
        some with none at all.
        """
        # Sufficient stock - comfortably covers outstanding demand
        cls.stock_c1 = StockItem.objects.create(part=cls.c1, quantity=500)
        cls.stock_m2 = StockItem.objects.create(part=cls.m2, quantity=40)
        cls.stock_n1 = StockItem.objects.create(part=cls.n1, quantity=60)

        # Insufficient stock - some cushion, but not enough to cover demand
        cls.stock_c3 = StockItem.objects.create(part=cls.c3, quantity=5)
        cls.stock_c4 = StockItem.objects.create(part=cls.c4, quantity=10)
        cls.stock_m3 = StockItem.objects.create(part=cls.m3, quantity=3)
        cls.stock_n2 = StockItem.objects.create(part=cls.n2, quantity=3)
        cls.stock_top2 = StockItem.objects.create(part=cls.top2, quantity=2)

        # No stock at all (deliberately no StockItem created): C2, M1, M4
        # (orphan), N3 (orphan), TOP1

    @classmethod
    def _create_future_orders_for_top_level_parts(cls):
        """Stage a year's worth of future build and sales orders against each
        top-level product, spread across varying dates and quantities - on top of
        the near-term orders already created in `_create_build_orders`/
        `_create_sales_orders`.
        """
        # (days out, quantity, completed)
        top1_build_schedule = [
            (15, 10, 0),
            (45, 6, 2),
            (80, 25, 0),
            (120, 4, 4),
            (165, 18, 0),
            (220, 12, 5),
            (300, 35, 0),
        ]
        # (days out, quantity, shipped)
        top1_sales_schedule = [
            (25, 5, 0),
            (70, 18, 6),
            (140, 3, 0),
            (210, 22, 0),
            (310, 9, 3),
        ]

        top2_build_schedule = [
            (20, 8, 0),
            (55, 15, 0),
            (95, 3, 1),
            (140, 20, 8),
            (190, 10, 0),
            (250, 28, 10),
            (340, 6, 0),
        ]
        top2_sales_schedule = [
            (30, 12, 0),
            (85, 4, 2),
            (150, 25, 0),
            (230, 7, 3),
            (320, 16, 0),
        ]

        cls.top1_future_builds = [
            Build.objects.create(
                part=cls.top1, quantity=quantity, completed=completed,
                reference=f'BO-91{idx:02d}',
                target_date=date.today() + timedelta(days=days),
            )
            for idx, (days, quantity, completed) in enumerate(top1_build_schedule, start=1)
        ]

        cls.top1_future_sales_orders = []
        for idx, (days, quantity, shipped) in enumerate(top1_sales_schedule, start=1):
            order = SalesOrder.objects.create(
                customer=cls.customer, reference=f'SO-ML-1{idx:02d}'
            )
            line = SalesOrderLineItem.objects.create(
                order=order, part=cls.top1, quantity=quantity, shipped=shipped,
                target_date=date.today() + timedelta(days=days),
            )
            cls.top1_future_sales_orders.append(line)

        cls.top2_future_builds = [
            Build.objects.create(
                part=cls.top2, quantity=quantity, completed=completed,
                reference=f'BO-92{idx:02d}',
                target_date=date.today() + timedelta(days=days),
            )
            for idx, (days, quantity, completed) in enumerate(top2_build_schedule, start=1)
        ]

        cls.top2_future_sales_orders = []
        for idx, (days, quantity, shipped) in enumerate(top2_sales_schedule, start=1):
            order = SalesOrder.objects.create(
                customer=cls.customer, reference=f'SO-ML-2{idx:02d}'
            )
            line = SalesOrderLineItem.objects.create(
                order=order, part=cls.top2, quantity=quantity, shipped=shipped,
                target_date=date.today() + timedelta(days=days),
            )
            cls.top2_future_sales_orders.append(line)

    @classmethod
    def _create_template_variant_fixture(cls):
        """Build an isolated template/variant BOM-inheritance scenario.

        `SHARED` is included in `TEMPLATE`'s BOM via an `inherited=True` BomItem,
        so it's effectively part of every variant's BOM too, without being
        redefined per-variant. A sales order against `VARIANT_A` should show up
        when querying `TEMPLATE`'s own forecast with `include_variants=True`.
        """
        cls.template = Part.objects.create(
            name='Widget Template', description='Template assembly for variant testing',
            is_template=True, assembly=True, component=False,
            purchaseable=False, salable=True,
        )
        cls.shared_component = Part.objects.create(
            name='Shared Mid-Level Component', description='Inherited across all TEMPLATE variants',
            assembly=True, component=True, purchaseable=False, salable=False,
        )
        cls.variant_a = Part.objects.create(
            name='Widget Variant A', description='Variant of the template assembly',
            variant_of=cls.template, assembly=True, component=False,
            purchaseable=False, salable=True,
        )
        cls.variant_b = Part.objects.create(
            name='Widget Variant B', description='Variant of the template assembly',
            variant_of=cls.template, assembly=True, component=False,
            purchaseable=False, salable=True,
        )

        # Refresh all four before wiring up the BomItem - see the MPTT tree_id
        # staleness note in _create_parts().
        for part in [cls.template, cls.shared_component, cls.variant_a, cls.variant_b]:
            part.refresh_from_db()

        cls.template_shared_bom_item = BomItem.objects.create(
            part=cls.template, sub_part=cls.shared_component, quantity=1, inherited=True,
        )

        cls.so_variant_a = SalesOrder.objects.create(customer=cls.customer, reference='SO-ML-008')
        cls.so_variant_a_line = SalesOrderLineItem.objects.create(
            order=cls.so_variant_a, part=cls.variant_a, quantity=9,
            target_date=date.today() + timedelta(days=18),
        )

    @classmethod
    def _create_deep_chain_fixture(cls):
        """Build a 10-tier-deep chain (D0 -> D1 -> ... -> D8 -> TOP1), with two
        shortcuts that create genuine multi-path diamonds at very different
        scales, plus extra sibling components/BOM lines for width:

            D0  D0B  D0C                         (tier 0 - leaves)
            D1  = 2x D0  + 1x D0B                 (tier 1)
            D2  = 1x D1  + 3x D0C                 (tier 2, also salable)
            D3  = 2x D2                           (tier 3)
            D4  = 1x D3                           (tier 4, gets its own build order)
            D5  = 3x D4                           (tier 5)
            D5B = 2x D4                           (tier 5 - sibling, shares D4 with D5)
            D6  = 1x D5  + 1x D5B                 (tier 6 - widened BOM)
            D7  = 2x D6  + 1x D5                  (tier 7 - shortcut: D5 direct AND via D6)
            D8  = 1x D7                           (tier 8)
            TOP1 gains: + 1x D8 (completes the 10-tier chain from D0)
                        + 5x D0 (shortcut: tier 0 straight to tier 9)

        So D0 is reachable from TOP1 via a 9-level chain AND a direct BOM line,
        and D4 is reachable from D7 via two internal diamonds (through D5 and
        D5B, which themselves both merge at D6). This stresses the batched
        upstream traversal with real depth, width, and multiple shortcut paths
        at different scales, on top of the existing diamond fixture above.
        """
        cls.d0 = Part.objects.create(
            name='Deep 0', description='Tier 0 leaf of the deep chain',
            purchaseable=True, component=True, assembly=False, salable=False,
        )
        cls.d0b = Part.objects.create(
            name='Deep 0B', description='Tier 0 leaf of the deep chain (width)',
            purchaseable=True, component=True, assembly=False, salable=False,
        )
        cls.d0c = Part.objects.create(
            name='Deep 0C', description='Tier 0 leaf of the deep chain (width)',
            purchaseable=True, component=True, assembly=False, salable=False,
        )
        cls.d1 = Part.objects.create(
            name='Deep 1', description='Tier 1 of the deep chain',
            assembly=True, component=True, purchaseable=False, salable=False,
        )
        cls.d2 = Part.objects.create(
            name='Deep 2', description='Tier 2 of the deep chain (also salable)',
            assembly=True, component=True, purchaseable=False, salable=True,
        )
        cls.d3 = Part.objects.create(
            name='Deep 3', description='Tier 3 of the deep chain',
            assembly=True, component=True, purchaseable=False, salable=False,
        )
        cls.d4 = Part.objects.create(
            name='Deep 4', description='Tier 4 of the deep chain',
            assembly=True, component=True, purchaseable=False, salable=False,
        )
        cls.d5 = Part.objects.create(
            name='Deep 5', description='Tier 5 of the deep chain',
            assembly=True, component=True, purchaseable=False, salable=False,
        )
        cls.d5b = Part.objects.create(
            name='Deep 5B', description='Tier 5 of the deep chain (sibling of Deep 5)',
            assembly=True, component=True, purchaseable=False, salable=False,
        )
        cls.d6 = Part.objects.create(
            name='Deep 6', description='Tier 6 of the deep chain',
            assembly=True, component=True, purchaseable=False, salable=False,
        )
        cls.d7 = Part.objects.create(
            name='Deep 7', description='Tier 7 of the deep chain',
            assembly=True, component=True, purchaseable=False, salable=False,
        )
        cls.d8 = Part.objects.create(
            name='Deep 8', description='Tier 8 of the deep chain',
            assembly=True, component=True, purchaseable=False, salable=False,
        )

        cls.deep_chain_parts = [
            cls.d0, cls.d0b, cls.d0c,
            cls.d1, cls.d2, cls.d3, cls.d4, cls.d5, cls.d5b, cls.d6, cls.d7, cls.d8,
        ]
        cls.all_parts += cls.deep_chain_parts

        # New root Parts can shift tree_id (and, for parts with a real
        # variant_of parent/child relationship, lft/rght too) for ALL existing
        # parts - see the MPTT tree_id staleness note in _create_parts(). This
        # affects not just cls.top1 (about to get new BomItems) but also the
        # cached template/variant objects from _create_template_variant_fixture,
        # whose get_ancestors()/get_descendants() queries depend on correct
        # lft/rght - refresh all of them now.
        for part in [
            *cls.deep_chain_parts, cls.top1,
            cls.template, cls.shared_component, cls.variant_a, cls.variant_b,
        ]:
            part.refresh_from_db()

        BomItem.objects.create(part=cls.d1, sub_part=cls.d0, quantity=2)
        BomItem.objects.create(part=cls.d1, sub_part=cls.d0b, quantity=1)

        BomItem.objects.create(part=cls.d2, sub_part=cls.d1, quantity=1)
        BomItem.objects.create(part=cls.d2, sub_part=cls.d0c, quantity=3)

        BomItem.objects.create(part=cls.d3, sub_part=cls.d2, quantity=2)
        BomItem.objects.create(part=cls.d4, sub_part=cls.d3, quantity=1)

        BomItem.objects.create(part=cls.d5, sub_part=cls.d4, quantity=3)
        BomItem.objects.create(part=cls.d5b, sub_part=cls.d4, quantity=2)

        BomItem.objects.create(part=cls.d6, sub_part=cls.d5, quantity=1)
        BomItem.objects.create(part=cls.d6, sub_part=cls.d5b, quantity=1)

        # Shortcut: D7 uses D5 both indirectly (via D6) and directly
        cls.d7_d6_item = BomItem.objects.create(part=cls.d7, sub_part=cls.d6, quantity=2)
        cls.d7_d5_item = BomItem.objects.create(part=cls.d7, sub_part=cls.d5, quantity=1)

        BomItem.objects.create(part=cls.d8, sub_part=cls.d7, quantity=1)

        # Completes the 10-tier chain from D0 up to TOP1 (tier 9), and adds the
        # top-to-bottom shortcut: TOP1 also uses D0 directly.
        cls.top1_d8_item = BomItem.objects.create(part=cls.top1, sub_part=cls.d8, quantity=1)
        cls.top1_d0_item = BomItem.objects.create(part=cls.top1, sub_part=cls.d0, quantity=5)

        # A build order and a sales order along the chain itself, so it isn't
        # relying solely on demand inherited from TOP1's own rich order set.
        cls.build_d4 = Build.objects.create(
            part=cls.d4, quantity=9, completed=3, reference='BO-9301',
            target_date=date.today() + timedelta(days=11),
        )
        cls.so_d2 = SalesOrder.objects.create(customer=cls.customer, reference='SO-ML-009')
        cls.so_d2_line = SalesOrderLineItem.objects.create(
            order=cls.so_d2, part=cls.d2, quantity=6, shipped=2,
            target_date=date.today() + timedelta(days=16),
        )


class PartFlagsTests(MultiLevelBOMTestCase):
    """Sanity checks that each part was created with the intended flags."""

    def test_components_are_purchaseable_leaf_parts(self):
        for part in [self.c1, self.c2, self.c3, self.c4]:
            self.assertFalse(part.assembly)
            self.assertTrue(part.purchaseable)
            self.assertTrue(part.component)

    def test_c4_is_also_salable(self):
        self.assertTrue(self.c4.salable)
        self.assertFalse(self.c1.salable)
        self.assertFalse(self.c2.salable)
        self.assertFalse(self.c3.salable)

    def test_sub_assemblies_are_assemblies(self):
        for part in [self.m1, self.m2, self.m3, self.m4]:
            self.assertTrue(part.assembly)
            self.assertTrue(part.component)

    def test_m2_is_also_purchaseable(self):
        self.assertTrue(self.m2.purchaseable)
        self.assertFalse(self.m1.purchaseable)
        self.assertFalse(self.m3.purchaseable)
        self.assertFalse(self.m4.purchaseable)

    def test_modules_are_assemblies(self):
        for part in [self.n1, self.n2, self.n3]:
            self.assertTrue(part.assembly)
            self.assertTrue(part.component)

    def test_n1_is_also_purchaseable(self):
        self.assertTrue(self.n1.purchaseable)
        self.assertFalse(self.n2.purchaseable)
        self.assertFalse(self.n3.purchaseable)

    def test_n2_is_also_salable(self):
        self.assertTrue(self.n2.salable)
        self.assertFalse(self.n1.salable)
        self.assertFalse(self.n3.salable)

    def test_top_level_products_are_assemblies_not_components(self):
        for part in [self.top1, self.top2]:
            self.assertTrue(part.assembly)
            self.assertFalse(part.component)
            self.assertTrue(part.salable)

    def test_top2_is_also_purchaseable(self):
        self.assertTrue(self.top2.purchaseable)
        self.assertFalse(self.top1.purchaseable)


class BOMStructureTests(MultiLevelBOMTestCase):
    """Sanity checks on the BOM quantities at each tier."""

    def _bom_quantity(self, part, sub_part):
        return BomItem.objects.get(part=part, sub_part=sub_part).quantity

    def test_tier1_bom_quantities(self):
        self.assertEqual(self._bom_quantity(self.m1, self.c1), 2)
        self.assertEqual(self._bom_quantity(self.m1, self.c2), 1)

        self.assertEqual(self._bom_quantity(self.m2, self.c2), 3)
        self.assertEqual(self._bom_quantity(self.m2, self.c3), 1)

        self.assertEqual(self._bom_quantity(self.m3, self.c3), 2)
        self.assertEqual(self._bom_quantity(self.m3, self.c4), 4)

        self.assertEqual(self._bom_quantity(self.m4, self.c1), 1)
        self.assertEqual(self._bom_quantity(self.m4, self.c4), 2)

    def test_tier2_bom_quantities(self):
        self.assertEqual(self._bom_quantity(self.n1, self.m1), 2)
        self.assertEqual(self._bom_quantity(self.n1, self.m2), 1)

        self.assertEqual(self._bom_quantity(self.n2, self.m2), 1)
        self.assertEqual(self._bom_quantity(self.n2, self.m3), 3)

        self.assertEqual(self._bom_quantity(self.n3, self.m3), 2)
        self.assertEqual(self._bom_quantity(self.n3, self.m4), 1)

    def test_tier3_bom_quantities(self):
        self.assertEqual(self._bom_quantity(self.top1, self.n1), 1)
        self.assertEqual(self._bom_quantity(self.top1, self.n2), 2)

        self.assertEqual(self._bom_quantity(self.top2, self.n1), 3)

    def test_each_tier1_assembly_has_exactly_two_bom_lines(self):
        for part in [self.m1, self.m2, self.m3, self.m4]:
            self.assertEqual(BomItem.objects.filter(part=part).count(), 2)

    def test_each_tier2_module_has_exactly_two_bom_lines(self):
        for part in [self.n1, self.n2, self.n3]:
            self.assertEqual(BomItem.objects.filter(part=part).count(), 2)

    def test_top2_has_exactly_one_bom_line(self):
        self.assertEqual(BomItem.objects.filter(part=self.top2).count(), 1)


class DiamondStructureTests(MultiLevelBOMTestCase):
    """Sanity checks on the diamond dependency and shared/orphaned intermediate structure."""

    def test_m2_reachable_from_top1_via_two_independent_paths(self):
        """TOP1 depends on M2 both via N1 and via N2 - a genuine diamond."""
        self.assertTrue(
            BomItem.objects.filter(part=self.n1, sub_part=self.m2).exists()
        )
        self.assertTrue(
            BomItem.objects.filter(part=self.n2, sub_part=self.m2).exists()
        )
        self.assertTrue(
            BomItem.objects.filter(part=self.top1, sub_part=self.n1).exists()
        )
        self.assertTrue(
            BomItem.objects.filter(part=self.top1, sub_part=self.n2).exists()
        )

    def test_m2_used_by_exactly_two_parents(self):
        parents = set(
            BomItem.objects.filter(sub_part=self.m2).values_list('part', flat=True)
        )
        self.assertEqual(parents, {self.n1.pk, self.n2.pk})

    def test_n1_shared_across_both_top_level_products(self):
        """N1 is used directly by both TOP1 and TOP2."""
        parents = set(
            BomItem.objects.filter(sub_part=self.n1).values_list('part', flat=True)
        )
        self.assertEqual(parents, {self.top1.pk, self.top2.pk})

    def test_m3_shared_between_n2_and_orphan_n3(self):
        """M3 is reused by both N2 (which reaches a product) and N3 (which doesn't)."""
        parents = set(
            BomItem.objects.filter(sub_part=self.m3).values_list('part', flat=True)
        )
        self.assertEqual(parents, {self.n2.pk, self.n3.pk})

    def test_m4_is_an_orphan_not_used_by_any_tier2_module(self):
        """M4 has its own BOM, but is never referenced as a sub_part anywhere."""
        self.assertFalse(BomItem.objects.filter(sub_part=self.m4).exclude(part=self.n3).exists())
        self.assertTrue(BomItem.objects.filter(part=self.m4).exists())

    def test_n3_is_an_orphan_not_used_by_any_top_level_product(self):
        """N3 has its own BOM, but is never referenced as a sub_part by TOP1/TOP2."""
        self.assertFalse(BomItem.objects.filter(sub_part=self.n3).exists())
        self.assertTrue(BomItem.objects.filter(part=self.n3).exists())


class OrderFixtureTests(MultiLevelBOMTestCase):
    """Sanity checks on the existing build/sales/purchase orders."""

    def test_purchase_orders_for_components(self):
        self.assertEqual(self.po_c1_line.part.part, self.c1)
        self.assertEqual(self.po_c1_line.quantity, 200)
        self.assertEqual(self.po_c1_line.received, 0)

        self.assertEqual(self.po_c3_line.part.part, self.c3)
        self.assertEqual(self.po_c3_line.quantity, 80)
        self.assertEqual(self.po_c3_line.received, 20)

    def test_multiple_purchase_orders_for_purchaseable_intermediates(self):
        # M2 - two POs, at 0% and 80% received
        self.assertEqual(PurchaseOrderLineItem.objects.filter(part=self.sp_m2).count(), 2)
        self.assertEqual(self.po_m2_a_line.quantity, 25)
        self.assertEqual(self.po_m2_a_line.received, 0)
        self.assertEqual(self.po_m2_b_line.quantity, 10)
        self.assertEqual(self.po_m2_b_line.received, 8)

        # N1 - two POs, at 0% and 60% received
        self.assertEqual(PurchaseOrderLineItem.objects.filter(part=self.sp_n1).count(), 2)
        self.assertEqual(self.po_n1_a_line.quantity, 15)
        self.assertEqual(self.po_n1_a_line.received, 0)
        self.assertEqual(self.po_n1_b_line.quantity, 20)
        self.assertEqual(self.po_n1_b_line.received, 12)

    def test_purchase_order_for_purchaseable_top_level_product(self):
        self.assertEqual(self.po_top2_line.part.part, self.top2)
        self.assertEqual(self.po_top2_line.quantity, 5)
        self.assertEqual(self.po_top2_line.received, 0)

    def test_multiple_near_term_sales_orders_for_top_level_products(self):
        # TOP1 - one pending, one partially shipped
        self.assertEqual(self.so_top1_a_line.quantity, 4)
        self.assertEqual(self.so_top1_a_line.shipped, 0)
        self.assertEqual(self.so_top1_b_line.quantity, 8)
        self.assertEqual(self.so_top1_b_line.shipped, 3)

        # TOP2 - one pending, one partially shipped
        self.assertEqual(self.so_top2_a_line.quantity, 10)
        self.assertEqual(self.so_top2_a_line.shipped, 0)
        self.assertEqual(self.so_top2_b_line.quantity, 6)
        self.assertEqual(self.so_top2_b_line.shipped, 1)

    def test_sales_orders_for_salable_intermediates(self):
        self.assertEqual(self.so_n2_line.part, self.n2)
        self.assertEqual(self.so_n2_line.quantity, 12)
        self.assertEqual(self.so_n2_line.shipped, 4)

        self.assertEqual(self.so_c4_line.part, self.c4)
        self.assertEqual(self.so_c4_line.quantity, 50)
        self.assertEqual(self.so_c4_line.shipped, 0)

    def test_multiple_near_term_build_orders_for_top_level_products(self):
        # TOP1 - two builds, at 20% and 0% complete
        self.assertEqual(self.build_top1_a.quantity, 10)
        self.assertEqual(self.build_top1_a.completed, 2)
        self.assertEqual(self.build_top1_b.quantity, 5)
        self.assertEqual(self.build_top1_b.completed, 0)

        # TOP2 - one build, nearly complete
        self.assertEqual(self.build_top2.part, self.top2)
        self.assertEqual(self.build_top2.quantity, 8)
        self.assertEqual(self.build_top2.completed, 6)

    def test_build_orders_for_mid_tier_assemblies(self):
        self.assertEqual(self.build_n1.part, self.n1)
        self.assertEqual(self.build_n1.quantity, 20)
        self.assertEqual(self.build_n1.completed, 5)

        self.assertEqual(self.build_n2.part, self.n2)
        self.assertEqual(self.build_n2.quantity, 12)
        self.assertEqual(self.build_n2.completed, 10)

        self.assertEqual(self.build_m2.part, self.m2)
        self.assertEqual(self.build_m2.quantity, 15)
        self.assertEqual(self.build_m2.completed, 0)

    def test_build_orders_for_orphan_assemblies(self):
        self.assertEqual(self.build_n3.part, self.n3)
        self.assertEqual(self.build_n3.quantity, 4)
        self.assertEqual(self.build_n3.completed, 1)

        self.assertEqual(self.build_m4.part, self.m4)
        self.assertEqual(self.build_m4.quantity, 6)
        self.assertEqual(self.build_m4.completed, 0)

    def test_build_lines_were_auto_generated_for_each_build(self):
        """Creating a Build should auto-generate BuildLine items matching its BOM."""
        self.assertEqual(self.build_top1_a.build_lines.count(), 2)  # N1, N2
        self.assertEqual(self.build_top2.build_lines.count(), 1)  # N1
        self.assertEqual(self.build_n1.build_lines.count(), 2)  # M1, M2
        self.assertEqual(self.build_n2.build_lines.count(), 2)  # M2, M3
        self.assertEqual(self.build_n3.build_lines.count(), 2)  # M3, M4
        self.assertEqual(self.build_m2.build_lines.count(), 2)  # C2, C3
        self.assertEqual(self.build_m4.build_lines.count(), 2)  # C1, C4

    def test_repeated_line_item_sales_order_for_top1(self):
        """A single SalesOrder can carry multiple lines for the same part, each
        with its own target_date - here, 5 lines for TOP1, a month apart.
        """
        self.assertEqual(len(self.so_top1_repeat_lines), 5)

        # All 5 lines belong to the same order
        order_pks = {line.order.pk for line in self.so_top1_repeat_lines}
        self.assertEqual(order_pks, {self.so_top1_repeat.pk})

        # Same part, same quantity, all fully outstanding - only date varies
        for line in self.so_top1_repeat_lines:
            self.assertEqual(line.part, self.top1)
            self.assertEqual(line.quantity, 7)
            self.assertEqual(line.shipped, 0)

        # Dates are 5 distinct values, each 30 days apart
        dates = sorted(line.target_date for line in self.so_top1_repeat_lines)
        self.assertEqual(len(set(dates)), 5)
        for earlier, later in zip(dates, dates[1:]):
            self.assertEqual((later - earlier).days, 30)

        # The order itself has exactly 5 line items
        self.assertEqual(self.so_top1_repeat.lines.count(), 5)


class StockLevelTests(MultiLevelBOMTestCase):
    """Sanity checks on the seeded stock levels across every tier."""

    def test_parts_with_sufficient_stock(self):
        self.assertEqual(self.c1.get_stock_count(), 500)
        self.assertEqual(self.m2.get_stock_count(), 40)
        self.assertEqual(self.n1.get_stock_count(), 60)

    def test_parts_with_insufficient_stock(self):
        self.assertEqual(self.c3.get_stock_count(), 5)
        self.assertEqual(self.c4.get_stock_count(), 10)
        self.assertEqual(self.m3.get_stock_count(), 3)
        self.assertEqual(self.n2.get_stock_count(), 3)
        self.assertEqual(self.top2.get_stock_count(), 2)

    def test_parts_with_no_stock(self):
        for part in [self.c2, self.m1, self.m4, self.n3, self.top1]:
            self.assertEqual(part.get_stock_count(), 0)
            self.assertFalse(StockItem.objects.filter(part=part).exists())

    def test_every_tier_has_at_least_one_insufficient_stock_part(self):
        insufficient = [self.c3, self.c4, self.m3, self.n2, self.top2]
        tiers = [
            [self.c1, self.c2, self.c3, self.c4],
            [self.m1, self.m2, self.m3, self.m4],
            [self.n1, self.n2, self.n3],
            [self.top1, self.top2],
        ]
        for tier in tiers:
            self.assertTrue(any(part in insufficient for part in tier))

    def test_both_orphans_and_top1_have_no_stock(self):
        """Orphan parts (M4, N3) and TOP1 have outstanding demand but no stock cushion."""
        for part in [self.m4, self.n3, self.top1]:
            self.assertEqual(part.get_stock_count(), 0)


class FutureOrderScheduleTests(MultiLevelBOMTestCase):
    """Sanity checks on the year-long future order schedule for each top-level product."""

    def test_seven_future_builds_per_top_level_part(self):
        self.assertEqual(len(self.top1_future_builds), 7)
        self.assertEqual(len(self.top2_future_builds), 7)

        for build in self.top1_future_builds:
            self.assertEqual(build.part, self.top1)
        for build in self.top2_future_builds:
            self.assertEqual(build.part, self.top2)

    def test_five_future_sales_orders_per_top_level_part(self):
        self.assertEqual(len(self.top1_future_sales_orders), 5)
        self.assertEqual(len(self.top2_future_sales_orders), 5)

        for line in self.top1_future_sales_orders:
            self.assertEqual(line.part, self.top1)
        for line in self.top2_future_sales_orders:
            self.assertEqual(line.part, self.top2)

    def test_total_build_order_counts_include_near_term_and_future(self):
        # 2 near-term (BO-9001, BO-9002) + 7 future
        self.assertEqual(Build.objects.filter(part=self.top1).count(), 9)
        # 1 near-term (BO-9003) + 7 future
        self.assertEqual(Build.objects.filter(part=self.top2).count(), 8)

    def test_total_sales_order_line_counts_include_near_term_and_future(self):
        # 2 near-term (SO-ML-001, SO-ML-002) + 5 future + 5 repeated-line (SO-ML-007)
        self.assertEqual(SalesOrderLineItem.objects.filter(part=self.top1).count(), 12)
        # 2 near-term (SO-ML-003, SO-ML-004) + 5 future
        self.assertEqual(SalesOrderLineItem.objects.filter(part=self.top2).count(), 7)

    def test_future_orders_are_spread_across_roughly_a_year(self):
        for builds in [self.top1_future_builds, self.top2_future_builds]:
            dates = [build.target_date for build in builds]
            self.assertEqual(len(dates), len(set(dates)))  # every date distinct
            self.assertGreater((max(dates) - min(dates)).days, 250)

        for lines in [self.top1_future_sales_orders, self.top2_future_sales_orders]:
            dates = [line.target_date for line in lines]
            self.assertEqual(len(dates), len(set(dates)))
            self.assertGreater((max(dates) - min(dates)).days, 250)

    def test_future_order_quantities_vary(self):
        for builds in [self.top1_future_builds, self.top2_future_builds]:
            quantities = {build.quantity for build in builds}
            self.assertGreater(len(quantities), 1)

        for lines in [self.top1_future_sales_orders, self.top2_future_sales_orders]:
            quantities = {line.quantity for line in lines}
            self.assertGreater(len(quantities), 1)

    def test_some_future_orders_are_partially_complete(self):
        """The future schedule includes a mix of untouched and partially-progressed orders."""
        for builds in [self.top1_future_builds, self.top2_future_builds]:
            completed = [build.completed for build in builds]
            self.assertTrue(any(c == 0 for c in completed))
            self.assertTrue(any(c > 0 for c in completed))

        for lines in [self.top1_future_sales_orders, self.top2_future_sales_orders]:
            shipped = [line.shipped for line in lines]
            self.assertTrue(any(s == 0 for s in shipped))
            self.assertTrue(any(s > 0 for s in shipped))


class TemplateVariantInheritedBOMTests(MultiLevelBOMTestCase):
    """Sanity checks on the isolated template/variant inherited-BOM fixture."""

    def test_template_and_variants_are_flagged_correctly(self):
        self.assertTrue(self.template.is_template)
        self.assertFalse(self.variant_a.is_template)
        self.assertFalse(self.variant_b.is_template)

        self.assertEqual(self.variant_a.variant_of, self.template)
        self.assertEqual(self.variant_b.variant_of, self.template)

    def test_shared_component_defined_only_on_template(self):
        """The BomItem is only ever created once, directly on TEMPLATE."""
        self.assertEqual(BomItem.objects.filter(sub_part=self.shared_component).count(), 1)
        self.assertEqual(self.template_shared_bom_item.part, self.template)
        self.assertTrue(self.template_shared_bom_item.inherited)

    def test_variants_inherit_the_shared_component_in_their_bom(self):
        """Neither variant has its own BomItem for SHARED, but get_bom_items()
        (which accounts for inheritance) should show it for both anyway.
        """
        for variant in [self.variant_a, self.variant_b]:
            self.assertFalse(
                BomItem.objects.filter(part=variant, sub_part=self.shared_component).exists()
            )
            effective_sub_parts = {
                item.sub_part for item in variant.get_bom_items(include_inherited=True)
            }
            self.assertIn(self.shared_component, effective_sub_parts)

    def test_variant_excluded_when_inheritance_not_considered(self):
        """Without include_inherited, a variant's own (empty) BOM doesn't show SHARED."""
        for variant in [self.variant_a, self.variant_b]:
            effective_sub_parts = {
                item.sub_part for item in variant.get_bom_items(include_inherited=False)
            }
            self.assertNotIn(self.shared_component, effective_sub_parts)

    def test_sales_order_exists_for_variant_a_only(self):
        self.assertEqual(self.so_variant_a_line.part, self.variant_a)
        self.assertEqual(self.so_variant_a_line.quantity, 9)
        self.assertEqual(self.so_variant_a_line.shipped, 0)

        self.assertFalse(SalesOrderLineItem.objects.filter(part=self.variant_b).exists())


class DeepChainTests(MultiLevelBOMTestCase):
    """Sanity checks on the 10-tier-deep chain grafted onto TOP1."""

    def _bom_quantity(self, part, sub_part):
        return BomItem.objects.get(part=part, sub_part=sub_part).quantity

    def test_deep_chain_is_included_in_all_parts(self):
        for part in self.deep_chain_parts:
            self.assertIn(part, self.all_parts)
        self.assertEqual(len(self.deep_chain_parts), 12)

    def test_linear_chain_quantities(self):
        self.assertEqual(self._bom_quantity(self.d1, self.d0), 2)
        self.assertEqual(self._bom_quantity(self.d1, self.d0b), 1)
        self.assertEqual(self._bom_quantity(self.d2, self.d1), 1)
        self.assertEqual(self._bom_quantity(self.d2, self.d0c), 3)
        self.assertEqual(self._bom_quantity(self.d3, self.d2), 2)
        self.assertEqual(self._bom_quantity(self.d4, self.d3), 1)
        self.assertEqual(self._bom_quantity(self.d5, self.d4), 3)
        self.assertEqual(self._bom_quantity(self.d5b, self.d4), 2)
        self.assertEqual(self._bom_quantity(self.d6, self.d5), 1)
        self.assertEqual(self._bom_quantity(self.d6, self.d5b), 1)
        self.assertEqual(self._bom_quantity(self.d7, self.d6), 2)
        self.assertEqual(self._bom_quantity(self.d7, self.d5), 1)
        self.assertEqual(self._bom_quantity(self.d8, self.d7), 1)
        self.assertEqual(self._bom_quantity(self.top1, self.d8), 1)
        self.assertEqual(self._bom_quantity(self.top1, self.d0), 5)

    def test_chain_is_exactly_ten_tiers_deep(self):
        """D0 (tier 0) up to TOP1 (tier 9) via the D8 path is 10 distinct tiers."""
        chain = [
            self.d0, self.d1, self.d2, self.d3, self.d4,
            self.d5, self.d6, self.d7, self.d8, self.top1,
        ]
        self.assertEqual(len(chain), 10)
        for sub_part, part in zip(chain, chain[1:]):
            self.assertTrue(BomItem.objects.filter(part=part, sub_part=sub_part).exists())

    def test_top1_reaches_d0_via_shortcut_and_full_chain(self):
        """D0 is reachable from TOP1 both directly and via the full 9-level chain."""
        self.assertTrue(BomItem.objects.filter(part=self.top1, sub_part=self.d0).exists())
        self.assertTrue(BomItem.objects.filter(part=self.top1, sub_part=self.d8).exists())

        parents_of_d0 = set(
            BomItem.objects.filter(sub_part=self.d0).values_list('part', flat=True)
        )
        self.assertEqual(parents_of_d0, {self.d1.pk, self.top1.pk})

    def test_d4_reachable_via_two_internal_diamonds(self):
        """D4 is used by both D5 and D5B, which both merge again at D6."""
        parents_of_d4 = set(
            BomItem.objects.filter(sub_part=self.d4).values_list('part', flat=True)
        )
        self.assertEqual(parents_of_d4, {self.d5.pk, self.d5b.pk})

        parents_of_d5 = set(
            BomItem.objects.filter(sub_part=self.d5).values_list('part', flat=True)
        )
        self.assertEqual(parents_of_d5, {self.d6.pk, self.d7.pk})

    def test_d6_bom_is_widened_by_the_sibling(self):
        self.assertEqual(BomItem.objects.filter(part=self.d6).count(), 2)

    def test_build_order_and_sales_order_along_the_chain(self):
        self.assertEqual(self.build_d4.part, self.d4)
        self.assertEqual(self.build_d4.quantity, 9)
        self.assertEqual(self.build_d4.completed, 3)

        self.assertEqual(self.so_d2_line.part, self.d2)
        self.assertEqual(self.so_d2_line.quantity, 6)
        self.assertEqual(self.so_d2_line.shipped, 2)

    def test_d2_is_salable(self):
        self.assertTrue(self.d2.salable)
        for part in [self.d0, self.d0b, self.d0c, self.d1, self.d3, self.d4]:
            self.assertFalse(part.salable)
