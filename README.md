if mapped_cls:
    mapped_cls = strip_generics(mapped_cls)
    # For dotted types like "OuterClass.InnerClass" the class that
    # owns the object is always the FIRST segment (class_1), not
    # the last.  e.g. final PaymentOrderSpec.PaymentOrderSpecBuilder
    # obj → obj's class is PaymentOrderSpec, not PaymentOrderSpecBuilder.
    # But if it is a package-qualified FQN (first char is lowercase,
    # e.g. "nl.acme.schemas...FilterPayload"), keep it intact so
    # _resolve_class_path can resolve it through fqn_to_path directly.
    if "." in mapped_cls and not mapped_cls[0].islower():
        mapped_cls = mapped_cls.split(".")[0]
