b_no_gen = strip_generics(b)
              cap = (b_no_gen[0].upper() + b_no_gen[1:]) if b_no_gen else b_no_gen
              if cap and cap in method_return_index:
                  return cap
  
              if b_no_gen in iface_to_impl_map:
                  impl_name = iface_to_impl_map[b_no_gen]
                  if impl_name in method_return_index:
                      return impl_name
  
              return b_no_gen
