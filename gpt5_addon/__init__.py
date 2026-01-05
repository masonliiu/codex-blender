bl_info = {
    "name": "GPT-5.2 Chat",
    "author": "Codex",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > GPT",
    "description": "Chat with OpenAI models from Blender",
    "category": "3D View",
}

import bpy


class GPT5AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    api_key: bpy.props.StringProperty(
        name="OpenAI API Key",
        subtype="PASSWORD",
        description="Stored locally in Blender preferences",
        default="",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "api_key")


class GPT5AddonProperties(bpy.types.PropertyGroup):
    prompt: bpy.props.StringProperty(
        name="Prompt",
        description="Message to send to the model",
        default="",
    )
    response: bpy.props.StringProperty(
        name="Response",
        description="Last response from the model",
        default="",
    )
    model: bpy.props.StringProperty(
        name="Model",
        description="OpenAI model name",
        default="gpt-5.2",
    )


class GPT5AddonPanel(bpy.types.Panel):
    bl_label = "GPT-5.2 Chat"
    bl_idname = "VIEW3D_PT_gpt5_chat"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GPT"

    def draw(self, context):
        layout = self.layout
        props = context.scene.gpt5_addon
        prefs = context.preferences.addons[__name__].preferences

        layout.prop(props, "model")
        layout.prop(props, "prompt")
        layout.operator("gpt5.send_message", icon="PLAY")
        layout.separator()
        if not prefs.api_key:
            layout.label(text="Set API key in Add-on Preferences", icon="ERROR")
        layout.label(text="Response")
        layout.prop(props, "response", text="")


class GPT5_OT_SendMessage(bpy.types.Operator):
    bl_idname = "gpt5.send_message"
    bl_label = "Send Message"
    bl_description = "Send the prompt to OpenAI"

    def execute(self, context):
        props = context.scene.gpt5_addon
        props.response = "API call not configured yet."
        return {'FINISHED'}


classes = (
    GPT5AddonPreferences,
    GPT5AddonProperties,
    GPT5AddonPanel,
    GPT5_OT_SendMessage,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.gpt5_addon = bpy.props.PointerProperty(type=GPT5AddonProperties)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.gpt5_addon


if __name__ == "__main__":
    register()
