# If the resolved type is an interface with an Impl, and the method
# definition is NOT in the interface file but IS in the Impl, resolve to Impl.
if b_no_gen in iface_to_impl_map:
    impl_name = iface_to_impl_map[b_no_gen]
    impl_path = type_to_path_full.get(impl_name)
    if impl_path:
        # Check if method being resolved exists in Impl but not in interface
        iface_path = type_to_path_full.get(b_no_gen)
        method_in_iface = bool(method_return_index.get(b_no_gen))
        method_in_impl  = bool(method_return_index.get(impl_name))
        if method_in_impl and not method_in_iface:
            return impl_name

return b_no_gen
