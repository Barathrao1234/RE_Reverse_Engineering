def build_interface_to_impl_map():
            iface_to_impl = {}
            for root, _, files in os.walk(app_folder):
                for file in files:
                    if not file.endswith(".java"):
                        continue
                    impl_name = file.replace(".java", "")
                    if impl_name.endswith("Impl"):
                        iface_name = impl_name[:-4]
                        iface_to_impl[iface_name] = impl_name
            return iface_to_impl

        iface_to_impl_map = build_interface_to_impl_map()
