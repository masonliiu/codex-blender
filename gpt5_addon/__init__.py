bl_info = {
    "name": "GPT-5.2 Chat",
    "author": "Mason Liu",
    "version": (0, 2, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > GPT",
    "description": "Chat with OpenAI models from Blender",
    "category": "3D View",
}

import json
import os
import queue
import threading
import urllib.error
import urllib.request

import bpy


class GPT5AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    api_key_source: bpy.props.EnumProperty(
        name="API Key Source",
        description="Where to read the OpenAI API key from",
        items=(
            ("PREFERENCES", "Preferences", "Store key in Blender preferences"),
            ("ENV", "Environment Variable", "Read key from an environment variable"),
        ),
        default="PREFERENCES",
    )
    api_key: bpy.props.StringProperty(
        name="OpenAI API Key",
        subtype="PASSWORD",
        description="Stored locally in Blender preferences",
        default="",
    )
    api_key_env_var: bpy.props.StringProperty(
        name="Env Var Name",
        description="Environment variable that holds the OpenAI API key",
        default="OPENAI_API_KEY",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "api_key_source")
        if self.api_key_source == "PREFERENCES":
            layout.prop(self, "api_key")
        else:
            layout.prop(self, "api_key_env_var")
        layout.operator("gpt5.debug_key", text="Debug Key")


class GPT5AddonHistoryItem(bpy.types.PropertyGroup):
    text: bpy.props.StringProperty(name="Text", default="")


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
    )
    model: bpy.props.StringProperty(
        name="Model",
        description="OpenAI model name",
        default="gpt-5.2",
    )
    history: bpy.props.CollectionProperty(type=GPT5AddonHistoryItem)
    history_index: bpy.props.IntProperty(
        name="History Index",
        default=-1,
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
        layout.label(text="History")
        layout.template_list(
            "GPT5_UL_prompt_history",
            "",
            props,
            "history",
            props,
            "history_index",
            rows=4,
        )
        row = layout.row(align=True)
        row.operator("gpt5.use_history", text="Use Selected")
        row.operator("gpt5.clear_history", text="Clear")
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

        api_key = _resolve_api_key(prefs)
        if not api_key:
            self.report({'ERROR'}, "OpenAI API key is missing")
            return {'CANCELLED'}

        prompt = props.prompt.strip()
        if not prompt:
            self.report({'WARNING'}, "Prompt is empty")
            return {'CANCELLED'}

        props.response = ""
        history_item = props.history.add()
        history_item.text = prompt
        props.history_index = len(props.history) - 1
        self._queue = queue.Queue()
        self._done = False
        self._error = None
        self._cancel_event = threading.Event()
        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        context.window_manager.modal_handler_add(self)

        self._thread = threading.Thread(
            target=_stream_openai_response,
            args=(
                self._queue,
                self._cancel_event,
                api_key,
                props.model.strip(),
                props.system_prompt.strip(),
                prompt,
            ),
            daemon=True,
        )
        self._thread.start()

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'TIMER':
            props = context.scene.gpt5_addon
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item["type"] == "delta":
                    props.response += item["text"]
                elif item["type"] == "error":
                    self._error = item["message"]
                elif item["type"] == "done":
                    self._done = True

            if self._error:
                props.response = f"Error: {self._error}"
                self.report({'ERROR'}, self._error)
                self._cleanup(context)
                return {'CANCELLED'}
            if self._done:
                self._cleanup(context)
                return {'FINISHED'}

        if event.type in {'ESC'}:
            self._cancel_event.set()
            self._cleanup(context)
            return {'CANCELLED'}

        return {'PASS_THROUGH'}

    def _cleanup(self, context):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None


class GPT5_UL_PromptHistory(bpy.types.UIList):
    bl_idname = "GPT5_UL_prompt_history"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        layout.label(text=item.text)


class GPT5_OT_UseHistory(bpy.types.Operator):
    bl_idname = "gpt5.use_history"
    bl_label = "Use Selected History"
    bl_description = "Copy the selected history item into the prompt"

    def execute(self, context):
        props = context.scene.gpt5_addon
        if props.history_index < 0 or props.history_index >= len(props.history):
            self.report({'WARNING'}, "No history item selected")
            return {'CANCELLED'}
        props.prompt = props.history[props.history_index].text
        return {'FINISHED'}


class GPT5_OT_ClearHistory(bpy.types.Operator):
    bl_idname = "gpt5.clear_history"
    bl_label = "Clear History"
    bl_description = "Clear prompt history"

    def execute(self, context):
        props = context.scene.gpt5_addon
        props.history.clear()
        props.history_index = -1
        return {'FINISHED'}


class GPT5_OT_DebugKey(bpy.types.Operator):
    bl_idname = "gpt5.debug_key"
    bl_label = "Debug API Key"
    bl_description = "Show API key length and last 4 characters"

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        api_key = _resolve_api_key(prefs)
        if not api_key:
            env_name = _env_var_name(prefs)
            env_present = bool(os.environ.get(env_name, "").strip())
            self.report({'ERROR'}, f"API key missing. Env var {env_name} set: {env_present}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"API key length: {len(api_key)}, last4: {api_key[-4:]}")
        return {'FINISHED'}


def _env_var_name(prefs):
    return (prefs.api_key_env_var or "OPENAI_API_KEY").strip() or "OPENAI_API_KEY"


def _resolve_api_key(prefs):
    if prefs.api_key_source == "ENV":
        env_name = _env_var_name(prefs)
        env_value = os.environ.get(env_name, "").strip()
        if env_value:
            return env_value
        if env_name != "OPENAI_API_KEY":
            fallback = os.environ.get("OPENAI_API_KEY", "").strip()
            if fallback:
                return fallback
        return ""
    prefs_value = prefs.api_key.strip()
    if prefs_value:
        return prefs_value
    env_name = _env_var_name(prefs)
    return os.environ.get(env_name, "").strip()


def _stream_openai_response(queue_out, cancel_event, api_key, model, system_prompt, prompt):
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
        "stream": True,
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
        with urllib.request.urlopen(request, timeout=60) as response:
            for raw_line in response:
                if cancel_event.is_set():
                    break
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                event_type = event.get("type")
                if event_type == "response.output_text.delta":
                    queue_out.put({"type": "delta", "text": event.get("delta", "")})
                elif event_type == "response.completed":
                    break
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else ""
        queue_out.put({"type": "error", "message": f"HTTP {exc.code}: {error_body}"})
        queue_out.put({"type": "done"})
        return
    except urllib.error.URLError as exc:
        queue_out.put({"type": "error", "message": f"Network error: {exc.reason}"})
        queue_out.put({"type": "done"})
        return

    queue_out.put({"type": "done"})


classes = (
    GPT5AddonPreferences,
    GPT5AddonHistoryItem,
    GPT5AddonProperties,
    GPT5AddonPanel,
    GPT5_OT_SendMessage,
    GPT5_UL_PromptHistory,
    GPT5_OT_UseHistory,
    GPT5_OT_ClearHistory,
    GPT5_OT_DebugKey,
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
