bl_info = {
    "name": "GPT-5.2 Chat",
    "author": "Codex",
    "version": (0, 2, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > GPT",
    "description": "Chat with OpenAI models from Blender",
    "category": "3D View",
}

import json
import urllib.error
import urllib.request

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
    system_prompt: bpy.props.StringProperty(
        name="System Prompt",
        description="Optional system guidance for the model",
        default="",
    )
    prompt: bpy.props.StringProperty(
        name="Prompt",
        description="Message to send to the model",
        default="",
    )
    response: bpy.props.StringProperty(
        name="Response",
        description="Last response from the model",
        default="",
        options={'MULTILINE'},
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
        layout.prop(props, "system_prompt")
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
        prefs = context.preferences.addons[__name__].preferences

        prompt = props.prompt.strip()
        if not prompt:
            self.report({'WARNING'}, "Prompt is empty")
            return {'CANCELLED'}
        if not prefs.api_key.strip():
            self.report({'ERROR'}, "OpenAI API key is missing")
            return {'CANCELLED'}

        try:
            props.response = _call_openai_response(
                api_key=prefs.api_key.strip(),
                model=props.model.strip(),
                system_prompt=props.system_prompt.strip(),
                prompt=prompt,
            )
        except RuntimeError as exc:
            props.response = f"Error: {exc}"
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        return {'FINISHED'}


def _call_openai_response(api_key, model, system_prompt, prompt):
    input_messages = []
    if system_prompt:
        input_messages.append({
            "role": "system",
            "content": [{"type": "text", "text": system_prompt}],
        })
    input_messages.append({
        "role": "user",
        "content": [{"type": "text", "text": prompt}],
    })
    payload = {
        "model": model,
        "input": input_messages,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url="https://api.openai.com/v1/responses",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else ""
        raise RuntimeError(f"HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Unexpected response from OpenAI") from exc

    output_text = []
    for output_item in parsed.get("output", []):
        for content_item in output_item.get("content", []):
            if content_item.get("type") == "output_text":
                output_text.append(content_item.get("text", ""))

    if not output_text:
        raise RuntimeError("No text output returned from OpenAI")

    return "".join(output_text).strip()


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
