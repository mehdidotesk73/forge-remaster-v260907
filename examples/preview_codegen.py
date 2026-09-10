from forge.msdk.msdk_build.discovery import discover_declarations
from forge.msdk.msdk_build.codegen import generate_module_source


def main():
    collector = discover_declarations("examples/declarations")

    resolved_names = {
        obj_def.api_name: (
            f"{obj_def.api_name.lower()}_edits_preview",
            f"{obj_def.api_name.lower()}_materialized_preview",
        )
        for obj_def in collector.objects
    }

    source = generate_module_source(
        object_defs=collector.objects,
        link_defs=collector.links,
        resolved_names=resolved_names,
    )
    print(source)


if __name__ == "__main__":
    main()
